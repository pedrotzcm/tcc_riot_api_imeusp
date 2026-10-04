from __future__ import annotations

import sys
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
from riotwatcher import ApiError
from requests.exceptions import ConnectionError as RequestsConnectionError

from src.db import store
from src.riot.client import get_watcher, network_retry
from src.riot.discover import FULL_BANDS

servidor = "br1"
queue = "RANKED_SOLO_5x5"
intervalo_log = 200
max_erros_rede = 25  # falhas de rede seguidas: para a run, o resto fica pra próxima rodada

# participantes das partidas do corpus ausentes da foto anterior 
sql_missing = """
WITH games AS (
    SELECT m.match_id, m.game_creation,
           (SELECT max(r.snapshot_at) FROM ladder_snapshot_runs r
             WHERE r.snapshot_at <= m.game_creation AND r.bands @> %(full_bands)s) AS s_before,
           m.payload->'metadata'->'participants' AS parts
    FROM matches_raw m
    JOIN corpus_membership c ON c.match_id = m.match_id AND c.corpus = %(corpus)s
)
SELECT DISTINCT pu.puuid
FROM games g
CROSS JOIN LATERAL jsonb_array_elements_text(g.parts) AS pu(puuid)
WHERE NOT EXISTS (SELECT 1 FROM ladder_snapshots l
                  WHERE l.snapshot_at = g.s_before AND l.puuid = pu.puuid)
  AND NOT EXISTS (SELECT 1 FROM rank_lookups rl
                  WHERE rl.puuid = pu.puuid AND rl.looked_up_at >= g.game_creation)
"""


@network_retry
def solo_entry(puuid: str) -> dict | None:
    # entry da Solo/Duo do jogador e  None = sem ranque na Solo/Duo
    watcher = get_watcher()
    for _ in range(6): 
        try:
            entries = watcher.league.by_puuid(servidor, puuid)

            break
        except ApiError as exc:
            if exc.response.status_code == 429:
                time.sleep(int(exc.response.headers.get("Retry-After", 5)))

                continue
            raise
    else:
        raise RuntimeError("erro 429")

    for entry in entries or []:
        if entry.get("queueType") == queue:
            return entry
    return None


def run(corpus: str) -> None:
    with store.get_conn() as conex:
        with conex.cursor() as cur:
            cur.execute(sql_missing, {"corpus": corpus, "full_bands": FULL_BANDS})

            puuids = []

            for row in cur.fetchall():
                puuids.append(row["puuid"])

        done = erros_rede_count = 0

        for puuid in puuids:
            try:
                entry = solo_entry(puuid) or {} 
            except (ApiError, RuntimeError):
                erros_rede_count = 0
                continue  
            except RequestsConnectionError:
                erros_rede_count += 1

                if erros_rede_count >= max_erros_rede:
                    raise SystemExit("Rank lookup abortado")
                continue

            erros_rede_count = 0
            with conex.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO rank_lookups
                        (puuid, looked_up_at, tier, division, league_points, wins, losses, hot_streak, fresh_blood)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (puuid, looked_up_at) DO NOTHING
                    """,
                    (puuid, datetime.now(timezone.utc), entry.get("tier", "UNRANKED"), entry.get("rank"),
                     entry.get("leaguePoints"), entry.get("wins"), entry.get("losses"),
                     entry.get("hotStreak"), entry.get("freshBlood")),
                )

            conex.commit()

            done += 1
            if done % intervalo_log == 0:
                print(f"  {done}/{len(puuids)} consultados", flush=True)

        print(f"Rank lookup {done} consultados", flush=True)


def main() -> None:
    args = sys.argv[1:]
    unknown = []

    for arg in args:
        if arg.startswith("--") and arg != "--corpus":
            unknown.append(arg)

    if unknown:
        raise SystemExit(f"flags desconhecidas: {unknown}")

    corpus = "q2_lp"
    if "--corpus" in args:
        corpus = args[args.index("--corpus") + 1]

    load_dotenv()
    run(corpus)


if __name__ == "__main__":
    main()
