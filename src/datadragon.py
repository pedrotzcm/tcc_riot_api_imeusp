from __future__ import annotations

import sys

import psycopg
import requests

from src.db.store import get_conn

from src.riot import client  

versions_url = "https://ddragon.leagueoflegends.com/api/versions.json"
champion_url = "https://ddragon.leagueoflegends.com/cdn/{version}/data/{locale}/champion.json"
idioma = "en_US"
timeout = 30


@client.network_retry
def get_json(url: str):
    return requests.get(url, timeout=timeout).json()


def ult_version() -> str:
    return get_json(versions_url)[0]


def versao_patch(patch: str) -> str | None:
#escolhe a  versão correspondente do patch
    for versao in get_json(versions_url):
        if versao.startswith(patch + "."):
            return versao
    return None


def fetch_champion_data(versao: str) -> list[dict]:
    # baixa o champion.json da versão e devolve uma linha por campeão
    url = champion_url.format(version=versao, locale=idioma)

    data = get_json(url)["data"]

    rows: list[dict] = []

    for champ in data.values():
        info = champ.get("info", {})
        rows.append({
            "champion_id": int(champ["key"]),
            "champion_key": champ["id"],
            "name": champ["name"],
            "attack": info.get("attack", 0),
            "magic": info.get("magic", 0),
            "defense": info.get("defense", 0),
            "difficulty": info.get("difficulty", 0),
            "tags": champ.get("tags", []),
            "ddragon_version": versao,
        })
    return rows


def gravar_campeoes(rows: list[dict], conex: psycopg.Connection) -> None:
  
    with conex.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO champion_static
                (champion_id, champion_key, name, attack, magic, defense,
                 difficulty, tags, ddragon_version, synced_at)
            VALUES
                (%(champion_id)s, %(champion_key)s, %(name)s, %(attack)s, %(magic)s,
                 %(defense)s, %(difficulty)s, %(tags)s, %(ddragon_version)s, now())
            ON CONFLICT (champion_id) DO UPDATE SET
                champion_key    = EXCLUDED.champion_key,
                name            = EXCLUDED.name,
                attack          = EXCLUDED.attack,
                magic           = EXCLUDED.magic,
                defense         = EXCLUDED.defense,
                difficulty      = EXCLUDED.difficulty,
                tags            = EXCLUDED.tags,
                ddragon_version = EXCLUDED.ddragon_version,
                synced_at       = now()
            """,
            rows,
        )


def sync(patch: str | None = None) -> None:
    # sincroniza champion_static com a versão do patch, ou a mais recente
    if patch:
        versao = versao_patch(patch)
    else:
        versao = ult_version()

    if versao is None:
        raise SystemExit(f"nenhuma versão Data Dragon para o patch {patch}")

    rows = fetch_champion_data(versao)

    with get_conn() as conex:
        gravar_campeoes(rows, conex)



def main() -> None:
    args = sys.argv[1:]

    unknown = []

    for arg in args:
        if arg.startswith("--") and arg != "--patch":
            unknown.append(arg)
    if unknown:
        raise SystemExit(f"flags desconhecidas: {unknown}")

    patch = None

    if "--patch" in args:
        patch = args[args.index("--patch") + 1]

    sync(patch=patch)  # o client já carrega o .env ao ser importado


if __name__ == "__main__":
    main()
