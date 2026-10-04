from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from riotwatcher import ApiError

from src.db import store
from src.riot import fetch, regions
from src.riot.client import get_watcher, network_retry

QUEUE_STR = "RANKED_SOLO_5x5"


@network_retry
def league_entries(platform: str, tier: str) -> list[dict]:
    # entradas do Challenger ou do Grandmaster (cada uma com puuid + leaguePoints)
    watcher = get_watcher()

    if tier == "CHALLENGER":
        fn = watcher.league.challenger_by_queue
    elif tier == "GRANDMASTER":
        fn = watcher.league.grandmaster_by_queue
    else:
        raise ValueError(f"tier inesperado: {tier}")

    league = fn(platform, QUEUE_STR)

    entradas = []

    for entrada in league.get("entries", []):
        if entrada.get("puuid"):
            entrada["tier"] = tier
            entradas.append(entrada)

    return entradas


def cohort_entries(platform: str, gm_top: int) -> list[dict]:
        # challengers + os top100 GMs --> apenas os melhors plauers

    entradas = league_entries(platform, "CHALLENGER")

    if gm_top > 0:
        gm = league_entries(platform, "GRANDMASTER")

        gm.sort(key=lambda entrada: entrada.get("leaguePoints", 0), reverse=True)
        entradas += gm[:gm_top]

    return entradas


def enqueue_region(region: str, days: int, now: datetime, conn, gm_top: int) -> None:
    # grava o snapshot da coorte e enfileira o histórico ranqueado de cada jogador na janela
    cluster = regions.cluster_for(region)

    start = int((now - timedelta(days=days)).timestamp())

    end = int(now.timestamp())

    entradas = cohort_entries(regions.platform_for(region), gm_top)
    store.upsert_cohort(entradas, region, now, conn)

    for entrada in entradas:

        try:
            ids = fetch.ids_partidas_janela(entrada["puuid"], cluster, start, end)

        except ApiError:
            continue

        store.mark_seen(ids, entrada["puuid"], conn)


def main() -> None:
    load_dotenv()

    days = int(os.getenv("COHORT_DAYS", "30"))
    gm_top = int(os.getenv("COHORT_GM_TOP", "100"))

    selected = []

    for region in os.getenv("COHORT_REGIONS", ",".join(regions.REGIONS)).split(","):
        region = region.strip().upper()

        if not region:
            continue

        if region not in regions.REGIONS:
            raise SystemExit(f"região desconhecida: {region}. Válidas: {list(regions.REGIONS)}")

        selected.append(region)

    now = datetime.now(timezone.utc)

    with store.get_conn() as conn:
        for region in selected:
            enqueue_region(region, days, now, conn, gm_top)


if __name__ == "__main__":
    main()
