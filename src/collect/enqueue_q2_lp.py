
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from riotwatcher import ApiError
from requests.exceptions import ConnectionError as RequestsConnectionError

from src.db import store
from src.riot import fetch, regions

load_dotenv()

corpus = "q2_lp"
region = "BR1"
overlap = timedelta(hours=6)
max_idade = timedelta(hours=3)
max_falhas = 0.01
max_atraso = 24


def enqueue(master_perc: float, conn) -> bool:
    # enfileira a janela nova, True se ficou completa
    now = datetime.now(timezone.utc)
    ultima_foto = store.latest_ladder_snapshot(conn)

    if ultima_foto is None or now - ultima_foto > max_idade:
        raise SystemExit(f"foto da ladder velha ({ultima_foto}); rode ladder_snapshot --discover antes")

    ultimo_fim = store.last_enqueue_window_end(corpus, conn)

    if ultimo_fim is None:
        # primeira rodada: desde a primeira foto
        with conn.cursor() as cur:
            cur.execute("SELECT min(snapshot_at) AS t FROM ladder_snapshot_runs")
            inicio = cur.fetchone()["t"]
    else:
        inicio = ultimo_fim - overlap

    # quem esteve na coorte em alguma foto que o filtro pode usar para partidas da janela
    puuids = store.get_cohort_puuids(conn, inicio - timedelta(hours=max_atraso), now, master_perc)

    cluster = regions.cluster_for(region)

    ts_ini, ts_fim = int(inicio.timestamp()), int(now.timestamp())
    queued = 0
    pendentes = puuids
    for passada in range(2):
        if passada == 1 and pendentes:
            time.sleep(60)
        falhas = []

        for puuid in pendentes:
            try:
                ids = fetch.ids_partidas_janela(puuid, cluster, ts_ini, ts_fim)
            except (ApiError, RequestsConnectionError):
                falhas.append(puuid)
                continue
            queued += store.mark_seen(ids, puuid, conn, queue=store.Q2_LP_QUEUE)
        pendentes = falhas

    completa = len(pendentes) <= max_falhas * max(len(puuids), 1)

    store.record_enqueue_run(corpus, inicio, now, len(puuids), len(pendentes), queued, completa, conn)
    return completa


filtro_sql = """
INSERT INTO corpus_membership (match_id, corpus)
WITH cand AS (
    SELECT m.match_id, m.game_creation, m.payload->'metadata'->'participants' AS parts
    FROM q2_lp_queue q

    JOIN matches_raw m ON m.match_id = q.match_id
    WHERE q.fetched
      AND m.queue_id = 420
      AND m.game_creation >= (SELECT min(snapshot_at) FROM ladder_snapshot_runs)

      AND NOT EXISTS (SELECT 1 FROM corpus_membership c

                      WHERE c.match_id = m.match_id AND c.corpus = %(corpus)s)
),
snap AS (
    -- foto completa mais recente ANTES do início (busca pelo índice da PK)

    SELECT c.match_id, c.parts, c.game_creation,

           (SELECT max(r.snapshot_at) FROM ladder_snapshot_runs r
             WHERE r.snapshot_at <= c.game_creation) AS snapshot_at
    FROM cand c
),
med AS (
    SELECT l.snapshot_at,

           percentile_cont(%(pct)s) WITHIN GROUP (ORDER BY l.league_points) AS cutoff
    FROM ladder_snapshots l
    WHERE l.tier = 'MASTER' AND l.snapshot_at IN (SELECT DISTINCT snapshot_at FROM snap)
    GROUP BY l.snapshot_at
)
SELECT DISTINCT s.match_id, %(corpus)s::text
FROM snap s
JOIN med ON med.snapshot_at = s.snapshot_at
CROSS JOIN LATERAL jsonb_array_elements_text(s.parts) AS pu(puuid)
JOIN ladder_snapshots l ON l.snapshot_at = s.snapshot_at AND l.puuid = pu.puuid
WHERE s.game_creation - s.snapshot_at <= make_interval(hours => %(max_stale)s)
  AND (l.tier IN ('CHALLENGER', 'GRANDMASTER')

       OR (l.tier = 'MASTER' AND l.league_points > med.cutoff))
ON CONFLICT (match_id, corpus) DO NOTHING
"""


def filtrar(master_perc: float, conn) -> None:
    with conn.cursor() as cur:
        cur.execute(filtro_sql, {"corpus": corpus, "pct": master_perc, "max_stale": max_atraso})
    conn.commit()


def main() -> None:
    flags = set(sys.argv[1:])

    if flags - {"--filtrar"}:
        raise SystemExit(f"flags desconhecidas: {sorted(flags - {'--filtrar'})}")
    master_perc = float(os.environ.get("Q2_MASTER_PERCENTILE", "0.5")) #diferença entre o low-mestre pro challenger é muito grande, vamos fazer um recorte do topo do mestre

    with store.get_conn() as conn:
        if "--filtrar" in flags:
            filtrar(master_perc, conn)
        elif not enqueue(master_perc, conn):
            sys.exit(1)


if __name__ == "__main__":
    main()
