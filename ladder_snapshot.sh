#!/usr/bin/env bash
# Foto da ladder Solo/Duo BR1 (diamante para cima) de hora em hora pra essa feature ser reconstruida no dataset final.


exec 201>/home/pedro.cruz/.ladder_snapshot.lock
flock -n 201 || { echo "[$(date '+%F %T')] rodada passada ainda rodando"; exit 0; }
set -uo pipefail
cd ~/TCC_Riot_API
set -a; . ./.env; set +a

PY=~/miniconda3/envs/tcc/bin/python
PGCTL=~/miniconda3/envs/tcc/bin/pg_ctl
PGDATA=/scratch/pedro.cruz/pgdata


$PGCTL -D "$PGDATA" status >/dev/null 2>&1 || { $PGCTL -D "$PGDATA" -l "$PGDATA/logfile" start >/dev/null 2>&1; sleep 6; }
echo "[$(date '+%F %T')] foto da ladder"
nice -n 19 $PY -m src.collect.ladder_snapshot
echo "[$(date '+%F %T')] rc=$?"
