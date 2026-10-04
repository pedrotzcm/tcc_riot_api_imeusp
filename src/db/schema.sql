
CREATE TABLE IF NOT EXISTS matches_raw (
    match_id        TEXT PRIMARY KEY,
    queue_id        INT  NOT NULL,
    patch           TEXT NOT NULL,
    game_version    TEXT NOT NULL,
    game_creation   TIMESTAMPTZ NOT NULL,
    game_duration_s INT  NOT NULL,
    region          TEXT,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload         JSONB NOT NULL
);
-- para bancos já existentes
ALTER TABLE matches_raw ADD COLUMN IF NOT EXISTS region TEXT;
CREATE INDEX IF NOT EXISTS matches_raw_patch_idx         ON matches_raw (patch);
CREATE INDEX IF NOT EXISTS matches_raw_game_creation_idx ON matches_raw (game_creation);
CREATE INDEX IF NOT EXISTS matches_raw_region_idx        ON matches_raw (region);

-- timeline minuto a minuto da partida
CREATE TABLE IF NOT EXISTS match_timelines_raw (
    match_id   TEXT PRIMARY KEY REFERENCES matches_raw (match_id),
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    payload    JSONB NOT NULL
);

-- roster cumulativo de high-elo (guarda o último tier/PDL visto)
CREATE TABLE IF NOT EXISTS summoners_high_elo (
    puuid         TEXT PRIMARY KEY,
    summoner_id   TEXT UNIQUE,
    tier          TEXT NOT NULL,
    league_points INT,
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);


-- source do PDL pré-start: snapshot mais recente com snapshot_at <= game_creation
CREATE TABLE IF NOT EXISTS ladder_snapshot_runs (
    snapshot_at TIMESTAMPTZ PRIMARY KEY,
    bands       TEXT[] NOT NULL,      
    n_players   INT  NOT NULL,
    source      TEXT NOT NULL         
);
CREATE TABLE IF NOT EXISTS ladder_snapshots (
    snapshot_at   TIMESTAMPTZ NOT NULL REFERENCES ladder_snapshot_runs (snapshot_at),
    puuid         TEXT NOT NULL,
    tier          TEXT NOT NULL,      
    division      TEXT NOT NULL,      
    league_points INT  NOT NULL,     
    wins          INT,                
    losses        INT,                
    hot_streak    BOOLEAN,
    fresh_blood   BOOLEAN,
    veteran       BOOLEAN,
    inactive      BOOLEAN,
    PRIMARY KEY (puuid, snapshot_at)
);
CREATE INDEX IF NOT EXISTS ladder_snapshots_at_idx ON ladder_snapshots (snapshot_at, tier);

-- rank de quem está fora da foto, medido depois da partida (fora das features pré-jogo pra quando tivermos players NAO high-elo)
CREATE TABLE IF NOT EXISTS rank_lookups (
    puuid         TEXT NOT NULL,
    looked_up_at  TIMESTAMPTZ NOT NULL,
    tier          TEXT NOT NULL,      
    division      TEXT,
    league_points INT,
    wins          INT,
    losses        INT,
    hot_streak    BOOLEAN,
    fresh_blood   BOOLEAN,
    PRIMARY KEY (puuid, looked_up_at)
);


CREATE TABLE IF NOT EXISTS mastery_photo_log (
    puuid        TEXT NOT NULL,
    fetched_at   TIMESTAMPTZ NOT NULL,
    n_champions  INT  NOT NULL,      
    source       TEXT NOT NULL,       
    PRIMARY KEY (puuid, fetched_at)
);
CREATE TABLE IF NOT EXISTS mastery_changes (
    puuid           TEXT NOT NULL,
    champion_id     INT  NOT NULL,
    fetched_at      TIMESTAMPTZ NOT NULL,
    mastery_points  BIGINT NOT NULL,
    mastery_level   INT  NOT NULL,
    last_play_time  TIMESTAMPTZ,
    PRIMARY KEY (puuid, champion_id, fetched_at)
);


CREATE TABLE IF NOT EXISTS corpus_enqueue_runs (
    corpus       TEXT NOT NULL,
    window_start TIMESTAMPTZ NOT NULL,
    window_end   TIMESTAMPTZ NOT NULL,
    n_players    INT  NOT NULL,
    n_failed     INT  NOT NULL,
    n_enqueued   INT  NOT NULL,
    complete     BOOLEAN NOT NULL,
    run_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (corpus, window_end)
);

-- coorte-alvo da Q1 multi-região, uma linha por (puuid, snapshot)

CREATE TABLE IF NOT EXISTS cohort_challenger (
    puuid         TEXT NOT NULL,
    region        TEXT NOT NULL,         
    league_points INT,
    tier          TEXT,                   
    snapshot_at   TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (puuid, snapshot_at)
);
CREATE INDEX IF NOT EXISTS cohort_challenger_region_idx ON cohort_challenger (region, snapshot_at);

-- fila de match_ids, fetched=false = pendente
CREATE TABLE IF NOT EXISTS match_ids_seen (
    match_id             TEXT PRIMARY KEY,
    discovered_via_puuid TEXT,
    discovered_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    fetched              BOOLEAN NOT NULL DEFAULT false,
    fetch_error          TEXT
);
CREATE INDEX IF NOT EXISTS match_ids_seen_unfetched_idx
    ON match_ids_seen (fetched) WHERE fetched = false AND fetch_error IS NULL;

-- fila própria da Q2 prospectiva (q2_lp), mesmo formato da match_ids_seen
CREATE TABLE IF NOT EXISTS q2_lp_queue (
    match_id             TEXT PRIMARY KEY,
    discovered_via_puuid TEXT,
    discovered_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    fetched              BOOLEAN NOT NULL DEFAULT false,
    fetch_error          TEXT
);
CREATE INDEX IF NOT EXISTS q2_lp_queue_unfetched_idx
    ON q2_lp_queue (fetched) WHERE fetched = false AND fetch_error IS NULL;


CREATE TABLE IF NOT EXISTS match_features (
    match_id     TEXT PRIMARY KEY REFERENCES matches_raw (match_id),
    patch        TEXT NOT NULL,
    duration_min REAL NOT NULL,
    team_100_win BOOLEAN NOT NULL,
    computed_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- pra marcar quais partidas pertencem a qual corpus (uma partida pode estar em vários)
CREATE TABLE IF NOT EXISTS corpus_membership (
    match_id  TEXT NOT NULL,
    corpus    TEXT NOT NULL,          
    added_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (match_id, corpus)
);
CREATE INDEX IF NOT EXISTS corpus_membership_corpus_idx ON corpus_membership (corpus);

-- OBSOLETA: substituída por player_champion_mastery, não é mais populada
CREATE TABLE IF NOT EXISTS match_participant_mastery (
    match_id        TEXT NOT NULL REFERENCES matches_raw (match_id),
    puuid           TEXT NOT NULL,
    champion_id     INT  NOT NULL,
    mastery_points  BIGINT NOT NULL,
    mastery_level   INT  NOT NULL,
    game_creation   TIMESTAMPTZ NOT NULL,
    collected_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (match_id, puuid)
);
CREATE INDEX IF NOT EXISTS mastery_puuid_idx ON match_participant_mastery (puuid);

-- maestria por jogador e campeão a cada snapshot; o join com a partida usa
-- o snapshot mais recente com snapshot_at <= game_creation
CREATE TABLE IF NOT EXISTS player_champion_mastery (
    puuid          TEXT NOT NULL,
    champion_id    INT  NOT NULL,
    mastery_points BIGINT NOT NULL,
    mastery_level  INT  NOT NULL,
    last_play_time TIMESTAMPTZ,
    snapshot_at    TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (puuid, champion_id, snapshot_at)
);

CREATE INDEX IF NOT EXISTS pcm_lookup_idx
    ON player_champion_mastery (puuid, champion_id, snapshot_at DESC);

-- features da Q1, 10 linhas por partida
CREATE TABLE IF NOT EXISTS player_match_features (
    match_id       TEXT NOT NULL REFERENCES matches_raw (match_id),
    participant_id INT  NOT NULL,
    puuid          TEXT,
    champion_id    INT,
    team           INT,
    role           TEXT,
    win            BOOLEAN,
    game_duration_min REAL,

    computed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (match_id, participant_id)
);
CREATE INDEX IF NOT EXISTS pmf_puuid_role_idx ON player_match_features (puuid, role);

-- maestria por partida (Q2)
CREATE TABLE IF NOT EXISTS match_mastery_features (
    match_id          TEXT PRIMARY KEY REFERENCES matches_raw (match_id),
    delta_days_avg    REAL,              
    is_proxy          BOOLEAN NOT NULL,  
    n_rescued_main    INT  NOT NULL,     
    n_imputed_main    INT  NOT NULL,     
    cov_t100          INT  NOT NULL,     
    cov_t200          INT  NOT NULL,
    mastery_avg_t100  REAL,              
    mastery_avg_t200  REAL,
    mastery_diff      REAL,             
    confident         BOOLEAN NOT NULL,  
    mastery_diff_top  REAL,              
    mastery_diff_jng  REAL,
    mastery_diff_mid  REAL,
    mastery_diff_bot  REAL,
    mastery_diff_sup  REAL,
    computed_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- contexto pré-jogo (Q2 q2_lp): match_participant_pregame (10 linhas por partida) e
-- match_pregame_features (1 por partida); recriadas por src/features/pregame_features.py

-- metadados de campeões (Data Dragon) -->  mix de dano do draft
CREATE TABLE IF NOT EXISTS champion_static (
    champion_id     INT  PRIMARY KEY,
    champion_key    TEXT NOT NULL,  
    name            TEXT NOT NULL,
    attack          INT  NOT NULL,
    magic           INT  NOT NULL,
    defense         INT  NOT NULL,
    difficulty      INT  NOT NULL,
    tags            TEXT[] NOT NULL,
    ddragon_version TEXT NOT NULL,
    synced_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- log de execuções da coleta
CREATE TABLE IF NOT EXISTS collection_runs (
    run_id            BIGSERIAL PRIMARY KEY,
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at       TIMESTAMPTZ,
    matches_fetched   INT NOT NULL DEFAULT 0,
    timelines_fetched INT NOT NULL DEFAULT 0,
    notes             TEXT
);
