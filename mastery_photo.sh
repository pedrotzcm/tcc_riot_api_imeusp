#!/usr/bin/env bash
# Fatia da foto de maestria do Master+ (1/24 por hora, cada jogador 1x/dia) RECONSTRUÇÃO DA FEATURE NO DATASET FINAL

exec 202>/home/pedro.cruz/.mastery_photo.lock
flock -n 202 || { echo "[$(date '+%F %T')] rodada passada ainda rodando"; exit 0; }
set -uo pipefail
cd ~/TCC_Riot_API
set -a; . ./.env; set +a
PY=~/miniconda3/envs/tcc/bin/python
PGCTL=~/miniconda3/envs/tcc/bin/pg_ctl
PGDATA=/scratch/pedro.cruz/pgdata

$PGCTL -D "$PGDATA" status >/dev/null 2>&1 || { $PGCTL -D "$PGDATA" -l "$PGDATA/logfile" start >/dev/null 2>&1; sleep 6; }
echo "[$(date '+%F %T')] fatia de maestria"
nice -n 19 $PY -m src.collect.mastery_photo
echo "[$(date '+%F %T')] rc=$?"
