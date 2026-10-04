from __future__ import annotations

from riotwatcher import ApiError

from src.riot.client import get_watcher, network_retry

PLATFORM = "br1"


@network_retry
def fetch_all_mastery(puuid: str) -> list[dict]:
    # uma entrada por campeão jogado (championId, championPoints, championLevel, lastPlayTime...)
    # [] se o jogador não tem maestrias (404)
    watcher = get_watcher()

    try:
        return watcher.champion_mastery.by_puuid(PLATFORM, puuid)
    except ApiError as exc:
        if exc.response.status_code == 404:
            return []
        raise
