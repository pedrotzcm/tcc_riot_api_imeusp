#!/usr/bin/env bash
exec 200>/home/pedro.cruz/.collect_key.lock
flock -n 200 || { echo "[$(date '+%F %T')] lock com outra coleta"; exit 0; }

set -uo pipefail
cd ~/TCC_Riot_API
set -a; . ./.env; set +a

PY=~/miniconda3/envs/tcc/bin/python
PSQL=~/miniconda3/envs/tcc/bin/psql
PGCTL=~/miniconda3/envs/tcc/bin/pg_ctl
PGDATA=/scratch/pedro.cruz/pgdata

log(){ echo "[$(date '+%F %T')] $*"; }
cnt(){ $PSQL "$DATABASE_URL" -tAc "$1" 2>/dev/null | tr -d '[:space:]'; }

ensure_pg(){ # sobe o postgres se estiver parado, na rede Vision, mais de 6s quebra
  $PGCTL -D "$PGDATA" status >/dev/null 2>&1 || { $PGCTL -D "$PGDATA" -l "$PGDATA/logfile" start >/dev/null 2>&1; sleep 6; }
}

retry_step(){ local name="$1"; shift; local i
  for i in 1 2 3 4; do
    ensure_pg; nice -n 19 "$@" && return 0
    [ $i -lt 4 ] && sleep 60
  done
  log "$name FALHOU apos 4 tentativas"; return 1; }

retry_step discover $PY -m src.collect.ladder_snapshot --discover \
  || { log "ABORTANDO: sem foto da ladder"; exit 1; }
retry_step datadragon $PY -m src.datadragon

ensure_pg; nice -n 19 $PY -m src.collect.enqueue_q2_lp

for tries in $(seq 200); do
  ensure_pg
  PEND=$(cnt "select count(*) from q2_lp_queue where not fetched and fetch_error is null")
  [ "${PEND:-1}" = 0 ] && break
  nice -n 19 $PY -m src.collect.run --collect --queue q2_lp
  sleep 30
done
[ "${PEND:-1}" = 0 ] 

retry_step filtrar          $PY -m src.collect.enqueue_q2_lp --filtrar
retry_step rank_lookup      $PY -m src.collect.rank_lookup
retry_step features         $PY -m src.features.match_features --corpus q2_lp
retry_step "pregame_features" $PY -m src.features.pregame_features