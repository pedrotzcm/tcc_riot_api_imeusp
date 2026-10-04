from __future__ import annotations

import time
from datetime import datetime, timezone

import psycopg
from loguru import logger
from riotwatcher import ApiError
from requests.exceptions import ChunkedEncodingError, ConnectionError as RequestsConnectionError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from src.db import store
from src.riot.client import get_watcher


ladder_retry = retry(
    retry=retry_if_exception_type((ChunkedEncodingError, RequestsConnectionError)),
    stop=stop_after_attempt(8),
    wait=wait_exponential(multiplier=2, min=5, max=120),
    reraise=True,
)

PLATFORM = "br1"
FILA = "RANKED_SOLO_5x5"
TOP_TIERS = ("CHALLENGER", "GRANDMASTER", "MASTER")

DIAMOND_DIVISIONS = ("I", "II", "III", "IV")


def with_429(fn, *args, count: int = 6, **kwargs):
    for _ in range(count):

        try:
            return fn(*args, **kwargs)

        except ApiError as exc:
            status = exc.response.status_code

            if status == 429 or status >= 500:
                if status == 429:
                    wait = int(exc.response.headers.get("Retry-After", 5))
                else:
                    wait = 30

                time.sleep(wait)
                continue

            raise

    raise RuntimeError("League-V4: 429/5xx persistente")


@ladder_retry
def apex_entries(tier: str) -> list[dict]:
    watcher = get_watcher()

    fn = {
        "CHALLENGER": watcher.league.challenger_by_queue,
        "GRANDMASTER": watcher.league.grandmaster_by_queue,
        "MASTER": watcher.league.masters_by_queue,
    }[tier]

    league = with_429(fn, PLATFORM, FILA)
    entries = []

    for entry in league.get("entries", []):
        if entry.get("puuid"):
            entries.append(dict(entry, tier=tier, rank="I"))

    return entries


@ladder_retry
def rank_page(tier: str, division: str, page: int) -> list[dict]:
    watcher = get_watcher()

    return with_429(watcher.league.entries, PLATFORM, FILA, tier, division, page=page) or []


def rank_entries(tier: str, division: str) -> list[dict]:
    out: list[dict] = []
    page = 1

    while True:
        rows = rank_page(tier, division, page)

        if not rows:
            return out

        for entry in rows:
            if entry.get("puuid"):
                out.append(dict(entry, tier=tier, rank=division))

        page += 1


def fetch_ladder(divisions: tuple[str, ...] = DIAMOND_DIVISIONS) -> list[dict]:

    entries: list[dict] = []
    for tier in TOP_TIERS:
        rows = apex_entries(tier)
        entries.extend(rows)

    for div in divisions:
        rows = rank_entries("DIAMOND", div)
        entries.extend(rows)

    return entries


def faixas_rank(divisions: tuple[str, ...]) -> list[str]:
    faixas = list(TOP_TIERS)

    for division in divisions:
        faixas.append(f"DIAMOND {division}")

    return faixas


FULL_BANDS = faixas_rank(DIAMOND_DIVISIONS)


def snapshot_ladder(conect: psycopg.Connection, divisions: tuple[str, ...] = DIAMOND_DIVISIONS,
                    source: str = "hourly") -> datetime:
    snapshot_at = datetime.now(timezone.utc)
    faixas = faixas_rank(divisions)

    entries = fetch_ladder(divisions)
    store.insert_ladder_snapshot(snapshot_at, entries, faixas, source, conect)

    unique = set()

    for entry in entries:
        unique.add(entry["puuid"])

    logger.info("Ladder gravada: {} jogadores ({} entradas)", len(unique), len(entries))

    return snapshot_at


def refresh_high_elo(conect: psycopg.Connection, divisions: tuple[str, ...] = DIAMOND_DIVISIONS) -> datetime:
    snapshot_at = snapshot_ladder(conect, divisions, source="discover")

    with conect.cursor() as cur:
        cur.execute(
            "SELECT puuid, tier, league_points FROM ladder_snapshots WHERE snapshot_at = %s AND tier = ANY(%s)",
            (snapshot_at, list(TOP_TIERS)),
        )
        top_tiers = cur.fetchall()

    by_tier = {}

    for tier in TOP_TIERS:
        by_tier[tier] = []

    for row in top_tiers:
        by_tier[row["tier"]].append({"puuid": row["puuid"], "leaguePoints": row["league_points"]})

    for tier in TOP_TIERS:
        store.upsert_summoners(tier, by_tier[tier], conect)

    return snapshot_at
