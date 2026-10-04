
from __future__ import annotations

import argparse
import sys

from dotenv import load_dotenv
from riotwatcher import ApiError
from requests.exceptions import ConnectionError as RequestsConnectionError

from src.db import store
from src.riot import discover, fetch, regions


batch_size = 500
intervalo_log = 100
max_erros_rede = 25  # falhas de rede seguidas: aborta a run


http_fix = {400}  


def http_status(exc: Exception) -> int | None:
    if isinstance(exc, ApiError):
        return exc.response.status_code
    return None


def run_discover(conex) -> None:
    print("Discover", flush=True)

    discover.refresh_high_elo(conex)

    puuids = store.get_puuids_for_matchlist(conex)  #chall+GM inteiros, Master acima da mediana de PDL

    print(f"{len(puuids)} puuids", flush=True)

    new_total = 0
    erros_rede_count = 0

    for i, puuid in enumerate(puuids, 1):
        try:
            match_ids = fetch.ids_partidas_puuid(puuid)
        except ApiError:
            continue
        except RequestsConnectionError:
            erros_rede_count += 1

            if erros_rede_count >= max_erros_rede:
                print(f"Discover abortado, {new_total} IDs novos", file=sys.stderr, flush=True)
                return

            continue

        erros_rede_count = 0
        new_total += store.mark_seen(match_ids, puuid, conex)

        if i % intervalo_log == 0:
            print(f"  {i}/{len(puuids)} puuids, {new_total} IDs novos", flush=True)

    print(f"Discover: {new_total} IDs novos", flush=True)


def run_collect(conex, queue: str = store.SHARED_QUEUE) -> None:
    #drenagem da fila
    print(f"Collect {queue}", flush=True)
    run_id = store.start_run(conex)

    matches_done = 0
    timelines_done = 0
    stored = 0
    errors = 0

    erros_rede_count = 0
    killed = False

    while not killed:
        batch = store.get_unfetched(batch_size, conex, queue=queue)
        if not batch:
            break

        for match_id in batch:
            if store.is_downloaded(match_id, conex):
                store.mark_fetched(match_id, conex, queue=queue)
                stored += 1
                continue

            try:
                region = regions.region_of_match_id(match_id)
                cluster = regions.cluster_for(region)
            except ValueError:
                store.mark_fetch_error(match_id, "UNKNOWN_REGION", conex, queue=queue)
                errors += 1
                continue

            # match
            try:
                payload = fetch.fetch_match(match_id, cluster=cluster)
            except (ApiError, RequestsConnectionError) as exc:
                status = http_status(exc)
                if status in http_fix:
                    store.mark_fetch_error(match_id, f"HTTP_{status}", conex, queue=queue)
                    errors += 1
                    erros_rede_count = 0  # teve resposta HTTP, a rede está ok
                    continue
                erros_rede_count += 1
                if erros_rede_count >= max_erros_rede:
                    print("Collect abortado", file=sys.stderr, flush=True)
                    killed = True

                    break
                continue

            if payload is None:
                store.mark_fetch_error(match_id, "NOT_FOUND", conex, queue=queue)

                errors += 1

                erros_rede_count = 0

                continue

            try:
                meta = fetch.meta_partida(payload)
            except ValueError:
                store.mark_fetch_error(match_id, "BAD_GAME_VERSION", conex, queue=queue)

                errors += 1

                erros_rede_count = 0

                continue

            store.upsert_match(
                match_id=meta["match_id"],
                queue_id=meta["queue_id"],
                patch=meta["patch"],
                game_version=meta["game_version"],
                game_creation=meta["game_creation"],
                game_duration_s=meta["game_duration_s"],
                payload=payload,
                conn=conex,
                region=region,
            )
            matches_done += 1

            # timeline
            try:
                timeline = fetch.fetch_timeline(match_id, cluster=cluster)
            except (ApiError, RequestsConnectionError) as exc:
                status = http_status(exc)

                if status in http_fix:
                    store.mark_fetched(match_id, conex, queue=queue)  # partida salva, timeline falhou em definitivo

                    errors += 1

                    erros_rede_count = 0

                    continue
                # transitório NÃO marca fetched, senão a partida fica sem timeline pra sempre
                
                erros_rede_count += 1

                if erros_rede_count >= max_erros_rede:
                    print("Collect abortado", file=sys.stderr, flush=True)

                    killed = True
                    break
                continue

            if timeline is not None:
                store.upsert_timeline(match_id, timeline, conex)
                timelines_done += 1

            store.mark_fetched(match_id, conex, queue=queue)
            erros_rede_count = 0

            if matches_done % intervalo_log == 0:
                print(f"  {matches_done} partidas, {timelines_done} timelines, {errors} erros", flush=True)

    store.finish_run(run_id, matches_done, timelines_done, conex, notes=f"queue={queue}")

    print(f"Collect {queue} run_id={run_id}: {matches_done} partidas, {timelines_done} timelines, "
          f"{stored} já no banco, {errors} erros", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--collect", action="store_true")

    parser.add_argument("--queue", choices=["geral", "q2_lp"], default="geral")
    args = parser.parse_args()

    if not args.discover and not args.collect:
        parser.print_help()
        sys.exit(1)
    if args.queue == "q2_lp" and args.discover:
        parser.error("--queue q2_lp só vale com --collect")

    load_dotenv()

    if args.queue == "q2_lp":
        queue = store.Q2_LP_QUEUE
    else:
        queue = store.SHARED_QUEUE

    with store.get_conn() as conex:
        if args.discover:
            run_discover(conex)

        if args.collect:
            run_collect(conex, queue=queue)


if __name__ == "__main__":
    main()
