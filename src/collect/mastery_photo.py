# Desenho e uso: ver MASTERY_PHOTO.md
from __future__ import annotations

import hashlib
import sys
import time
from datetime import datetime, timedelta, timezone

import psycopg
from dotenv import load_dotenv
from riotwatcher import ApiError
from requests.exceptions import ConnectionError as RequestsConnectionError

from src.db import store
from src.riot.mastery import fetch_all_mastery

slots = 24
limite_recuperacao = timedelta(hours=26)  # sem foto há mais que isso: entra na próxima hora

idade_min_fatia = timedelta(hours=8)
max_erros_rede = 25  # falhas de rede seguidas: para a run, quem faltou entra nas próximas horas
intervalo_log = 200
flags = {"--full", "--max-players"}


def slot_of(puuid: str) -> int:
    return int(hashlib.md5(puuid.encode()).hexdigest(), 16) % slots #PRA PARCELAR A COLETA


def changed_rows(campeoes: list[dict], stored: dict[int, int]) -> list[dict]:
    #atualização de pontos de maestria.
    rows = []
    for campeao in campeoes:
        if stored.get(campeao["championId"]) != campeao["championPoints"]:
            rows.append(campeao)

    return rows


def roster(conex: psycopg.Connection) -> list[str]:
    # Master+ inteiro na foto de rank mais recente.
    with conex.cursor() as cur:
        cur.execute(
            """
            SELECT puuid FROM ladder_snapshots
            WHERE snapshot_at = (SELECT max(snapshot_at) FROM ladder_snapshot_runs)
              AND tier IN ('CHALLENGER', 'GRANDMASTER', 'MASTER')
            """
        )
        puuids = []

        for row in cur.fetchall():
            puuids.append(row["puuid"])
        return puuids


def last_photo(conex: psycopg.Connection, puuids: list[str]) -> dict[str, datetime]:
    with conex.cursor() as cur:
        cur.execute(
            "SELECT puuid, max(fetched_at) AS t FROM mastery_photo_log WHERE puuid = ANY(%s) GROUP BY puuid",
            (puuids,),
        )
        fotos = {}

        for row in cur.fetchall():
            fotos[row["puuid"]] = row["t"]
        return fotos


def pick(players: list[str], last: dict[str, datetime], now: datetime, full: bool,
         max_players: int | None) -> list[str]:
    if full:
        return players

    catch_up = []
    in_slot = []

    for player in players:
        if last.get(player) is None or now - last[player] > limite_recuperacao:
            catch_up.append(player)
        elif slot_of(player) == now.hour and now - last[player] > idade_min_fatia:
            in_slot.append(player)

    catch_up.sort(key=lambda player: last.get(player) or datetime.min.replace(tzinfo=timezone.utc))
    chosen = catch_up + in_slot
    if max_players:
        return chosen[:max_players]

    return chosen


def fetch_mastery(puuid: str) -> list[dict]:
    # Maestria do jogador
    for _ in range(6):
        try:
            return fetch_all_mastery(puuid)
        except ApiError as exc:
            if exc.response.status_code == 429:
                time.sleep(int(exc.response.headers.get("Retry-After", 5)))
                continue
            raise
    raise RuntimeError("429 persistente")


def save(puuid: str, campeoes: list[dict], source: str, conex: psycopg.Connection) -> int:
    fetched_at = datetime.now(timezone.utc)
    with conex.cursor() as cur:
        cur.execute(
            """
            SELECT DISTINCT ON (champion_id) champion_id, mastery_points
            FROM mastery_changes WHERE puuid = %s
            ORDER BY champion_id, fetched_at DESC
            """,
            (puuid,),
        )
        stored = {}
        for row in cur.fetchall():
            stored[row["champion_id"]] = row["mastery_points"]

        rows = changed_rows(campeoes, stored)

        if rows:
            params = []
            for campeao in rows:
                ultimo_jogo = None
                if campeao.get("lastPlayTime"):
                    ultimo_jogo = datetime.fromtimestamp(campeao["lastPlayTime"] / 1000, tz=timezone.utc)
                params.append((puuid, campeao["championId"], fetched_at, campeao["championPoints"],
                               campeao["championLevel"], ultimo_jogo))
            cur.executemany(
                """
                INSERT INTO mastery_changes
                    (puuid, champion_id, fetched_at, mastery_points, mastery_level, last_play_time)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                params,
            )
        cur.execute(
            "INSERT INTO mastery_photo_log (puuid, fetched_at, n_champions, source) VALUES (%s, %s, %s, %s)",
            (puuid, fetched_at, len(campeoes), source),
        )
    conex.commit()

    return len(rows)


def run(full: bool, max_players: int | None) -> int:
    now = datetime.now(timezone.utc)
    if full:
        source = "full"
    else:
        source = "slot"

    with store.get_conn() as conex:
        players = roster(conex)

        if not players:
            return 1
        chosen = pick(players, last_photo(conex, players), now, full, max_players)

        done = erros_rede_count = 0
        for puuid in chosen:
            try:
                campeoes = fetch_mastery(puuid)
            except (ApiError, RuntimeError) as exc:
                print(f"maestria {puuid[:12]} falhou: {exc}", flush=True)
                erros_rede_count = 0
                continue
            except RequestsConnectionError as exc:
                erros_rede_count += 1

                print(f"maestria {puuid[:12]} rede {erros_rede_count}/{max_erros_rede}: {exc}", flush=True)
                if erros_rede_count >= max_erros_rede:
                    break

                continue
            erros_rede_count = 0
            save(puuid, campeoes, source, conex)
            done += 1
            if done % intervalo_log == 0:
                print(f"  {done}/{len(chosen)} jogadores", flush=True)

    return 0


def main() -> None:
    args = sys.argv[1:]
    unknown = []

    for arg in args:
        if arg.startswith("--") and arg not in flags:
            unknown.append(arg)
    if unknown:
        raise SystemExit(f"flags desconhecidas: {unknown}")

    full = "--full" in args
    max_players = 1500
    if "--max-players" in args:
        max_players = int(args[args.index("--max-players") + 1])

    load_dotenv()
    if full:
        sys.exit(run(full, None))
    sys.exit(run(full, max_players))


if __name__ == "__main__":
    main()
