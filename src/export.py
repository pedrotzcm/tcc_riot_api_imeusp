from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from src.db.store import get_conn

load_dotenv()

# só tabelas planas
tabelas_ok = {"match_features", "champion_static", "player_champion_mastery",
              "match_mastery_features", "player_match_features", "player_style_features",
              "corpus_membership", "match_pregame_features", "match_participant_pregame"}

flags = {"--table", "--out", "--where"}


def export_table(table: str, out: Path, where: str | None = None) -> None:
    if table not in tabelas_ok:
        raise ValueError(f"Tabela '{table}' não permitida para export. Use uma de: {sorted(tabelas_ok)}")

    condicao = ""

    if where:
        condicao = f" WHERE {where}"

    with get_conn() as conex, conex.cursor() as cur:
        cur.execute(f"SELECT * FROM {table}{condicao}")

        df = pd.DataFrame(cur.fetchall())

    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)

    print(f"export {table}: {len(df)} linhas, {df.shape[1]} colunas -> {out}", flush=True)


def main() -> None:
    args = sys.argv[1:]
    unknown = []

    for arg in args:
        if arg.startswith("--") and arg not in flags:
            unknown.append(arg)

    if unknown:
        raise SystemExit(f"flags desconhecidas: {unknown}")

    def valor(flag: str, padrao: str | None) -> str | None:
        if flag not in args:
            return padrao
        posicao = args.index(flag) + 1

        if posicao >= len(args):
            raise SystemExit(f"{flag} precisa de um valor")
        return args[posicao]

    table = valor("--table", "match_features")

    out = valor("--out", f"{table}.parquet")
    where = valor("--where", None)
    export_table(table, Path(out), where=where)


if __name__ == "__main__":
    main()
