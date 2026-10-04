# ranque e maestria de cada jogador no início da partida (Q2 q2_lp), sem leakage
import os
import sys

from dotenv import load_dotenv

from src.db.store import get_conn
from src.riot.discover import FULL_BANDS

CORPUS = "q2_lp"
TETO_MAESTRIA = 1_000_000  # pontos de maestria acima disso contam como o teto --> considerando que acima disso, tanto faz 

DDL_TABELAS = """
DROP TABLE IF exists match_pregame_features;
drop TABLE if EXISTS match_participant_pregame;
CREATE TABLE match_participant_pregame (
    match_id   TEXT NOT NULL references matches_raw (match_id),
    puuid             TEXT NOT NULL,
    team              INT  not NULL,
    role  TEXT,
    champion_id       INT,
    -- ranque
    snapshot_at   TIMESTAMPTZ,       
    staleness_min REAL,              
    tier   TEXT,              
    division          TEXT,
    league_points INT,
    lp_scale          INT,              
    wins INT,
    losses            INT,
    hot_streak   BOOLEAN,
    fresh_blood BOOLEAN,
    games_between     INT,               
    lp_exact BOOLEAN,          
    lp_source         TEXT,              
    lookup_delay_h   REAL,             
    -- maestria
    mastery_at        TIMESTAMPTZ,      
    mastery_age_h     REAL,            
    mastery_points BIGINT,            
    mastery_level   INT,
    mastery_last_play TIMESTAMPTZ,      
    primary key (match_id, puuid)
);
CREATE TABLE match_pregame_features (
    match_id TEXT PRIMARY KEY references matches_raw (match_id),
    -- ranque (só PDL da foto)
    snapshot_at   TIMESTAMPTZ,
    staleness_min   REAL,
    n_lp_t100   INT  not null,    
    n_lp_t200          INT not null,
    n_lookup_t100 INT NOT null,    
    n_lookup_t200  INT  not null,
    n_exact   INT NOT null,    
    lp_avg_t100   REAL,
    lp_avg_t200        REAL,
    lp_diff REAL,             
    lp_avg             REAL,             
    lp_diff_top        REAL,             
    lp_diff_jng  REAL,
    lp_diff_mid REAL,
    lp_diff_bot        REAL,
    lp_diff_sup   REAL,
    n_chall_t100    INT not NULL, n_chall_t200 INT NOT NULL,
    n_gm_t100       INT NOT NULL, n_gm_t200      INT not null,
    n_master_t100  INT NOT null, n_master_t200 INT NOT NULL,
    n_diamond_t100 INT not null, n_diamond_t200 INT NOT null,
    hot_streak_t100 INT NOT null, hot_streak_t200 INT not NULL,
    -- maestria (só medidos, pontos com teto)
    n_mastery_t100   INT  not null,    -- jogadores com maestria medida (0-5)
    n_mastery_t200   INT NOT null,
    mastery_avg_t100 REAL,
    mastery_avg_t200 REAL,
    mastery_diff       REAL,            
    mastery_diff_top  REAL,             
    mastery_diff_jng  REAL,
    mastery_diff_mid REAL,
    mastery_diff_bot   REAL,
    mastery_diff_sup  REAL,
    mastery_age_h_max  REAL,            
    computed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

SQL_JOGADORES = """
INSERT INTO match_participant_pregame
WITH games AS (

    select m.match_id, m.game_creation,
           (SELECT max(r.snapshot_at) FROM ladder_snapshot_runs r
             where r.snapshot_at <= m.game_creation AND r.bands = %(faixas_completas)s) as s_before,
           (SELECT min(r.snapshot_at) from ladder_snapshot_runs r
             WHERE r.snapshot_at >= m.game_creation + make_interval(secs => m.game_duration_s)
                                   + interval '10 minutes'
               AND r.bands = %(faixas_completas)s) as s_after,
           m.payload->'info'->'participants' AS parts
    FROM matches_raw m
    JOIN corpus_membership c ON c.match_id = m.match_id and c.corpus = %(corpus)s
),
parts as (
    select g.match_id, g.game_creation, g.s_before, g.s_after,
           p->>'puuid' AS puuid, CAST(p->>'teamId' as int) as team, NULLIF(p->>'teamPosition', '') AS role,
           CAST(p->>'championId' AS int) as champion_id
    FROM games g, jsonb_array_elements(g.parts) p
),
joined as (
    SELECT p.match_id, p.puuid, p.team, p.role, p.champion_id, p.game_creation, p.s_before,
           b.puuid IS not null AS in_snapshot,
           COALESCE(b.tier, k.tier)   AS tier,
           COALESCE(b.division, k.division)  as division,
           COALESCE(b.league_points, k.league_points) as lp,
           COALESCE(b.wins, k.wins)  as wins,
           COALESCE(b.losses, k.losses) AS losses,
           COALESCE(b.hot_streak, k.hot_streak)  AS hot_streak,
           COALESCE(b.fresh_blood, k.fresh_blood) AS fresh_blood,
           (a.wins + a.losses) - (b.wins + b.losses)  AS games_between,
           k.looked_up_at,
           ml.fetched_at   AS mastery_at,
           mc.mastery_points, mc.mastery_level, mc.last_play_time
    from parts p
    LEFT JOIN ladder_snapshots b ON b.snapshot_at = p.s_before AND b.puuid = p.puuid
    LEFT join ladder_snapshots a ON a.snapshot_at = p.s_after  and a.puuid = p.puuid
    -- fora da foto: consulta individual mais próxima depois do início
    left JOIN LATERAL (
        select rl.* FROM rank_lookups rl
        where rl.puuid = p.puuid and rl.looked_up_at >= p.game_creation
        ORDER by rl.looked_up_at
        LIMIT 1
    ) k on b.puuid IS null
    left JOIN lateral (
        SELECT l.fetched_at FROM mastery_photo_log l
        WHERE l.puuid = p.puuid AND l.fetched_at <= p.game_creation
          AND l.fetched_at >= p.game_creation - interval '14 days'
        order BY l.fetched_at desc
        limit 1

    ) ml ON true
    LEFT join lateral (
        select c.mastery_points, c.mastery_level, c.last_play_time FROM mastery_changes c
        WHERE c.puuid = p.puuid AND c.champion_id = p.champion_id AND c.fetched_at <= ml.fetched_at
        order BY c.fetched_at DESC
        limit 1
    ) mc ON ml.fetched_at IS NOT null
)
SELECT match_id, puuid, team, role, champion_id,
       s_before,
       CAST(EXTRACT(EPOCH FROM (game_creation - s_before)) / 60.0 AS real),
       tier, division, lp,
       case
         WHEN tier IN ('MASTER', 'GRANDMASTER', 'CHALLENGER') THEN 2800 + lp
         ELSE 400 * array_position(ARRAY['IRON','BRONZE','SILVER','GOLD','PLATINUM','EMERALD','DIAMOND'], tier) - 400
            + 100 * (4 - array_position(ARRAY['I','II','III','IV'], division))
            + lp
       END,
       wins, losses, hot_streak, fresh_blood,
       games_between,
       games_between = 1,
       CASE when in_snapshot THEN 'snapshot' WHEN tier IS NOT NULL then 'lookup' END,
       CASE WHEN NOT in_snapshot THEN CAST(EXTRACT(EPOCH from (looked_up_at - game_creation)) / 3600.0 AS real) END,
       mastery_at,
       CAST(EXTRACT(EPOCH from (game_creation - mastery_at)) / 3600.0 as real),
       case WHEN mastery_at IS NOT NULL then COALESCE(mastery_points, 0) END,
       case WHEN mastery_at IS not null THEN COALESCE(mastery_level, 0) end,
       last_play_time
from joined
ON CONFLICT (match_id, puuid) DO NOTHING
"""

SQL_PARTIDAS = """
insert INTO match_pregame_features (
    match_id, snapshot_at, staleness_min, n_lp_t100, n_lp_t200, n_lookup_t100, n_lookup_t200, n_exact,
    lp_avg_t100, lp_avg_t200, lp_diff, lp_avg,
    lp_diff_top, lp_diff_jng, lp_diff_mid, lp_diff_bot, lp_diff_sup,
    n_chall_t100, n_chall_t200, n_gm_t100, n_gm_t200, n_master_t100, n_master_t200,
    n_diamond_t100, n_diamond_t200, hot_streak_t100, hot_streak_t200,
    n_mastery_t100, n_mastery_t200, mastery_avg_t100, mastery_avg_t200, mastery_diff,
    mastery_diff_top, mastery_diff_jng, mastery_diff_mid, mastery_diff_bot, mastery_diff_sup,
    mastery_age_h_max
)
WITH p AS (
    select *,
           CASE when lp_source = 'snapshot'  then lp_scale end as lp_pre,
           case WHEN mastery_points IS not NULL
                THEN LEAST(mastery_points, %(teto_maestria)s) end as mastery_capped
    FROM match_participant_pregame
),
t AS (
    select match_id, team,
           count(lp_pre) AS n_lp,
           avg(lp_pre)  as lp_avg,
           count(*) filter (WHERE lp_source = 'lookup') AS n_lookup,
           count(*) filter (WHERE lp_source = 'snapshot' and lp_exact)   as n_exact,
           count(*) filter (WHERE lp_source = 'snapshot' and tier = 'CHALLENGER')  AS n_chall,
           count(*) filter (where lp_source = 'snapshot' AND tier = 'GRANDMASTER') AS n_gm,
           count(*) filter (where lp_source = 'snapshot' and tier = 'MASTER')   as n_master,
           count(*) filter (WHERE lp_source = 'snapshot' and tier = 'DIAMOND') as n_diamond,
           count(*) filter (where lp_source = 'snapshot' and hot_streak) AS n_hot,
           count(mastery_capped)                                           as n_mastery,
           avg(mastery_capped) as mastery_avg,
           max(mastery_age_h)   as mastery_age_max,
           max(snapshot_at)                                                AS snapshot_at,
           max(staleness_min) AS staleness_min
    from p
    group by match_id, team
),
lobby as (
    SELECT match_id, avg(lp_pre) AS lp_avg from p group by match_id
),
role_pair AS (
    select a.match_id, a.role,
           (a.lp_pre - b.lp_pre)                 as lp_d,
           (a.mastery_capped - b.mastery_capped) AS m_d
    FROM p a
    JOIN p b ON b.match_id = a.match_id AND b.role = a.role and b.team = 200
    where a.team = 100 AND a.role IS NOT null
),
role_agg as (
    SELECT match_id,
           max(lp_d) FILTER (where role = 'TOP')   AS lp_top,
           max(lp_d) FILTER (where role = 'JUNGLE') as lp_jng,
           max(lp_d) FILTER (where role = 'MIDDLE')   AS lp_mid,
           max(lp_d) filter (WHERE role = 'BOTTOM')  AS lp_bot,
           max(lp_d) FILTER (WHERE role = 'UTILITY') AS lp_sup,
           max(m_d)  filter (WHERE role = 'TOP')  AS m_top,
           max(m_d) filter (WHERE role = 'JUNGLE')   as m_jng,
           max(m_d) filter (where role = 'MIDDLE')  as m_mid,
           max(m_d)  FILTER (where role = 'BOTTOM')   as m_bot,
           max(m_d)  FILTER (WHERE role = 'UTILITY') AS m_sup
    from role_pair
    group by match_id
)
SELECT a.match_id, a.snapshot_at, a.staleness_min, a.n_lp, b.n_lp, a.n_lookup, b.n_lookup,
       a.n_exact + b.n_exact,
       CAST(a.lp_avg AS real), CAST(b.lp_avg AS real), CAST(a.lp_avg - b.lp_avg as real), CAST(l.lp_avg AS real),
       CAST(r.lp_top AS real), CAST(r.lp_jng AS real), CAST(r.lp_mid as real), CAST(r.lp_bot AS real), CAST(r.lp_sup as real),
       a.n_chall, b.n_chall, a.n_gm, b.n_gm, a.n_master, b.n_master,
       a.n_diamond, b.n_diamond, a.n_hot, b.n_hot,
       a.n_mastery, b.n_mastery, CAST(a.mastery_avg AS real), CAST(b.mastery_avg AS real),
       CAST(a.mastery_avg - b.mastery_avg AS real),
       CAST(r.m_top AS real), CAST(r.m_jng AS real), CAST(r.m_mid AS real), CAST(r.m_bot AS real), CAST(r.m_sup as real),
       CAST(GREATEST(a.mastery_age_max, b.mastery_age_max) AS real)
FROM t a
JOIN t b on b.match_id = a.match_id AND b.team = 200
LEFT JOIN lobby l ON l.match_id = a.match_id
left JOIN role_agg r ON r.match_id = a.match_id
where a.team = 100
"""


def recalcular(corpus: str = CORPUS, teto_maestria: int = TETO_MAESTRIA, conn=None) -> None:
    # conn: conexão já aberta (o validar_q2_lp.py usa um schema descartável), sem ela abre a do .env
    if conn is None:
        with get_conn() as conexao:
            recalcular(corpus, teto_maestria, conexao)

        return

    with conn.cursor() as cursor:
        cursor.execute(DDL_TABELAS)
        cursor.execute(SQL_JOGADORES, {"corpus": corpus, "faixas_completas": FULL_BANDS})
        cursor.execute(SQL_PARTIDAS, {"teto_maestria": teto_maestria})

        conn.commit()

        cursor.execute(
            """
            SELECT count(*)   AS jogadores,
                   count(*) FILTER (WHERE lp_source = 'snapshot')      as foto,
                   count(*) FILTER (WHERE lp_source = 'lookup')   as consulta,
                   count(*) filter (where lp_exact) AS exatos,
                   count(mastery_points)   AS com_maestria,
                   round(CAST(avg(staleness_min) AS numeric), 1)               AS defasagem_ranque_min,
                   round(CAST(avg(mastery_age_h) AS numeric), 1)               AS defasagem_maestria_h
            from match_participant_pregame
            """
        )

        resumo = cursor.fetchone()
        cursor.execute("SELECT count(*) AS n FROM match_pregame_features")

        partidas = cursor.fetchone()["n"]

    total = max(resumo["jogadores"], 1)
    print(
        f"pregame_features ({corpus}): {partidas} partidas | {resumo['jogadores']} jogadores | "
        f"ranque: foto {resumo['foto'] / total:.1%}, exatos {resumo['exatos'] / total:.1%}, consulta {resumo['consulta'] / total:.1%}, "
        f"defasagem {resumo['defasagem_ranque_min']} min | "
        f"maestria: {resumo['com_maestria'] / total:.1%}, defasagem {resumo['defasagem_maestria_h']} h",
        flush=True,
    )


def principal():
    load_dotenv()

    if len(sys.argv) > 1:
        raise SystemExit("sem flags; use PREGAME_CORPUS e PREGAME_TETO_MAESTRIA")

    corpus = os.environ.get("PREGAME_CORPUS", CORPUS)
    teto_maestria = int(os.environ.get("PREGAME_TETO_MAESTRIA", TETO_MAESTRIA))

    recalcular(corpus=corpus, teto_maestria=teto_maestria)


if __name__ == "__main__":
    principal()
