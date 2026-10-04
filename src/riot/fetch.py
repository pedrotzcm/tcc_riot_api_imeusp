from __future__ import annotations

import time
from datetime import datetime, timezone

from riotwatcher import ApiError

from src.riot.client import get_watcher, network_retry

CLUSTER = "americas"  # default do pipeline BR original
FILA_SOLO = 420  # fila ranqueada solo
PAGE = 100  # máximo de match IDs por chamada da Match-V5


def none_if_404(call, *args):

    try:
        return call(*args)

    except ApiError as exc:
        if exc.response.status_code == 404:
            return None

        raise


@network_retry
def ids_partidas_puuid(puuid: str, count: int = 100, cluster: str = CLUSTER) -> list[str]:
    watcher = get_watcher()

    return watcher.match.matchlist_by_puuid(cluster, puuid, queue=FILA_SOLO, count=count)


@network_retry
def pagina_partidas(
    puuid: str, cluster: str, start: int,
    start_time: int | None, end_time: int | None, tries: int = 6,
) -> list[str]:
 
    watcher = get_watcher()

    for _ in range(tries):
        try:
            return watcher.match.matchlist_by_puuid(
                cluster, puuid, queue=FILA_SOLO, count=PAGE,
                start=start, start_time=start_time, end_time=end_time,
            )

        except ApiError as exc:
            if exc.response.status_code == 429:
                wait = int(exc.response.headers.get("Retry-After", 5))

                time.sleep(wait)
                continue

            raise

    return []


def ids_partidas_janela(
    puuid: str, cluster: str, start_time: int, end_time: int,
) -> list[str]:

    ids: list[str] = []
    start = 0

    while True:
        page = pagina_partidas(puuid, cluster, start, start_time, end_time)

        ids.extend(page)
        if len(page) < PAGE:
            break

        start += PAGE

    return ids


@network_retry
def fetch_match(match_id: str, cluster: str = CLUSTER) -> dict | None:
    # payload completo da partida
    return none_if_404(get_watcher().match.by_id, cluster, match_id)


@network_retry
def fetch_timeline(match_id: str, cluster: str = CLUSTER) -> dict | None:
     #timeline frame a frame
    return none_if_404(get_watcher().match.timeline_by_match, cluster, match_id)


def extract_patch(game_version: str) -> str:
    parts = game_version.split(".")

    if len(parts) < 2:
        raise ValueError(f"gameVersion inesperado: {game_version!r}")

    return f"{parts[0]}.{parts[1]}"


def meta_partida(payload: dict) -> dict:
    info = payload["info"]

    return {
        "match_id": payload["metadata"]["matchId"],
        "queue_id": info["queueId"],
        "patch": extract_patch(info["gameVersion"]),
        "game_version": info["gameVersion"],
        "game_creation": datetime.fromtimestamp(info["gameCreation"] / 1000, tz=timezone.utc),
        "game_duration_s": info["gameDuration"],
    }
