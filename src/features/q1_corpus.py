# manifesto do corpus da Q1 (q1_challenger) em corpus_membership
import os
import sys

from dotenv import load_dotenv

from src.db.store import get_conn
from src.riot.regions import REGIONS

CORPUS = "q1_challenger"
DESDE = "2026-08-08"


def filtrar_corpus(desde: str = DESDE) -> None:
    # a conexão do get_conn já commita ao sair do with
    with get_conn() as conexao, conexao.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO corpus_membership (match_id, corpus)
            SELECT DISTINCT m.match_id, %(corpus)s
            FROM matches_raw m
            CROSS JOIN LATERAL jsonb_array_elements_text(m.payload->'metadata'->'participants') AS p(puuid)
            JOIN (SELECT DISTINCT puuid, region FROM cohort_challenger) c
              ON c.puuid = p.puuid AND c.region = m.region
            WHERE m.region = ANY(%(regioes)s) AND m.game_creation >= %(desde)s
              AND m.game_creation <= (SELECT max(snapshot_at) FROM cohort_challenger)
            ON CONFLICT DO NOTHING
            """,
            {"corpus": CORPUS, "desde": desde, "regioes": list(REGIONS)},
        )
        novas = cursor.rowcount

        cursor.execute(
            """
            SELECT m.region, count(*) AS n
            FROM corpus_membership cm JOIN matches_raw m ON m.match_id = cm.match_id
            WHERE cm.corpus = %s
            GROUP BY m.region ORDER BY m.region
            """,
            (CORPUS,),
        )
        por_regiao = {}
        for linha in cursor.fetchall():
            por_regiao[linha["region"]] = linha["n"]



def principal() -> None:
    load_dotenv()
    if len(sys.argv) > 1:
        raise SystemExit("sem flags")

    filtrar_corpus(desde=os.environ.get("Q1_DESDE", DESDE))


if __name__ == "__main__":
    principal()
