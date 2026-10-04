from __future__ import annotations
from pathlib import Path
import pandas as pd

features_path = Path("data/player_style_features_q1.parquet")
fotos_path = Path("data/cohort_challenger.csv")

# colunas que não entram no modelo
context = ("match_id", "participant_id", "computed_at", "puuid", "champion_id", "team",
           "role", "win", "region", "cohort_region", "patch", "game_creation", "autofill",
           "game_duration_min", "window_min_15")

# booleanas
bool_features = ["first_blood_15", "first_tower_15"]

# chaves do groupby
keys = ["puuid","role"]

ordem_cols = ["puuid", "region", "role", "main_role", "is_main_role",
              "n_matches", "n_matches_all", "frac_role", "n_late",
              "win_rate", "autofill_rate", "n_champions", "top_champion_id", "top_champion_share",
              "mean_duration_min", "first_game", "last_game", "frac_own_window",
              "tier", "in_final_foto", "first_foto", "last_foto", "lp_first", "lp_last"]

# (agregação, só janela própria, arquivo de saída)
variantes = [("mean", False, "data/player_role_q1.parquet"),
             ("mean", True, "data/player_role_q1_own.parquet"),
             ("median", False, "data/player_role_q1_median.parquet"),
             ("median", True, "data/player_role_q1_median_own.parquet")]


def feature_columns(matches: pd.DataFrame) -> list[str]:
    cols = []
    for c in matches.columns:
        if c not in context: cols.append(c)
    return cols


def load_matches(path: str | Path = features_path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    df = df[(df["cohort_region"]==df["region"]) & df["role"].notna()]  # coorte na própria região, role conhecida

    return df.reset_index(drop=True)

def rank_snapshots(path: str | Path = fotos_path) -> pd.DataFrame:
    ph = pd.read_csv(path)
    ph["snapshot_at"] = pd.to_datetime(ph["snapshot_at"], utc=True).dt.tz_convert("America/Sao_Paulo")
    ph['tier'] = ph['tier'].fillna("CHALLENGER")  # legado: coleta antiga só tinha Challenger

    return ph


def snapshot_resumo(fotos: pd.DataFrame) -> pd.DataFrame:
    g = fotos.sort_values("snapshot_at").groupby(["puuid", "region"])
    out = pd.concat([
        g["tier"].last(),
        g["snapshot_at"].first().rename("first_foto"),
        g["snapshot_at"].last().rename("last_foto"),
        g["league_points"].first().rename("lp_first"),
        g["league_points"].last().rename("lp_last"),
    ], axis=1)

    final_ts = fotos.groupby("region")["snapshot_at"].max()
    out["in_final_foto"] = out["last_foto"]==out.index.get_level_values("region").map(final_ts)
    return out


def build(matches: pd.DataFrame, fotos: pd.DataFrame, agg: str = "mean",
          own_window: bool = False) -> pd.DataFrame:
    if agg not in ("mean", "median"):
        raise ValueError(f"agg deve ser 'mean' ou 'median', não {agg!r}")

    feats = feature_columns(matches)

    bools=[]
    cont=[]
    for c in feats:
        if c in bool_features:
            bools.append(c)
        else:
            cont.append(c)

    # quem saiu da coorte de alto-elo
    resumo = snapshot_resumo(fotos)

    m = matches.merge(resumo[["first_foto", "last_foto"]],
                      left_on=["puuid", "region"], right_index=True, how="left")

    # janela própria = [primeira foto − 30 dias, última foto]; fora dela, partidas vieram de outros jogadores
    start = m["first_foto"]-pd.Timedelta(days=30)
    m["in_window"] = (m["game_creation"] >= start) & (m["game_creation"] <= m["last_foto"])
    if own_window:
        m = m[m["in_window"]]
    g = m.groupby(keys)

    table = pd.concat([
        g["region"].first(),
        g.size(),
        g["pos_spread_late"].count(),
        g["win"].mean(),
        g["autofill"].mean(),
        g["champion_id"].nunique(),
        g["game_duration_min"].mean(),
        g["game_creation"].min(),
        g["game_creation"].max(),
        g["in_window"].mean(),
        g[cont].agg(agg),
        g[bools].mean(),
    ], axis=1)

    nomes = ["region", "n_matches", "n_late", "win_rate", "autofill_rate",
             "n_champions", "mean_duration_min", "first_game", "last_game",
             "frac_own_window"]
    table.columns = nomes + cont + bools

    champ = (m.groupby(keys + ["champion_id"]).size().rename("n").reset_index()
             .sort_values(["n", "champion_id"], ascending=[False, True])
             .drop_duplicates(keys).set_index(keys))

    table["top_champion_id"] = champ["champion_id"]
    table["top_champion_share"] = champ["n"] / table["n_matches"]  #para ver os campeões mais jogados por cluster

    table = table.reset_index()

    table["n_matches_all"] = table.groupby("puuid")["n_matches"].transform("sum")
    #fração das partidas do jogador nessa role --> player pode jogar em outras 
    table["frac_role"] = table["n_matches"] / table["n_matches_all"]

    table = table.sort_values(["puuid", "n_matches", "last_game"], ascending=[True, False, False])

    principal = table.drop_duplicates("puuid")[["puuid", "role"]]
    principal = principal.rename(columns={"role": "main_role"})

    table = table.merge(principal, on="puuid")
    table["is_main_role"] = table["role"]==table["main_role"]
    table = table.merge(resumo, left_on=["puuid", "region"], right_index=True, how="left")

    return table[ordem_cols + feats].sort_values(["region", "role", "puuid"]).reset_index(drop=True)


def count_by_role(table: pd.DataFrame, floor: int = 30, main_only: bool = True) -> pd.DataFrame:
    t = table[table["n_matches"] >= floor]  #mínimo de partidas na role
    if main_only:
        t = t[t["is_main_role"]]
    return t.groupby(["role","region"]).size().reset_index(name="n_jogadores")


def main() -> None:
    matches = load_matches()
    fotos = rank_snapshots()

    for agg, own_window, out in variantes:
        build(matches, fotos, agg=agg, own_window=own_window).to_parquet(out, index=False)


if __name__ == "__main__":
    main()
