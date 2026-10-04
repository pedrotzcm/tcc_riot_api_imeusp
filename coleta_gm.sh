#!/usr/bin/env bash
exec 200>/home/pedro.cruz/.collect_key.lock
flock -n 200 || { echo "[$(date '+%F %T')] lock com outra coleta"; exit 0; }

# Coleta de Q1: Challenger + top100 GM, Coreia, Brasil e Europa --> vai servir para o perfilamento dos players.



cd ~/TCC_Riot_API
source ~/miniconda3/etc/profile.d/conda.sh
conda activate tcc

export $(grep -v '^#' .env | grep -E '^(RIOT_API_KEY|DATABASE_URL)=')
pg_ctl -D /scratch/pedro.cruz/pgdata status >/dev/null 2>&1 || \
  pg_ctl -D /scratch/pedro.cruz/pgdata -l /scratch/pedro.cruz/pgdata/logfile start >/dev/null 2>&1
PY=~/miniconda3/envs/tcc/bin/python
echo "===== coleta_gm  ====="
ok=0


for i in 1 2 3 4 5 6; do #6 tentativas para coleta pra tentar robustez contra DNS
  nice -n 19 $PY -m src.collect.enqueue_cohort --days 30 --gm-top 100 && { ok=1; break; }
  echo "[enqueue falhou (DNS?), aguardando 90s]"; sleep 90
done
if [ $ok -eq 1 ]; then
  echo "[COLLECT inicio]"
  nice -n 19 $PY -m src.collect.run --collect


  # scripts para a montagem das features de clusterização
  nice -n 19 $PY -m src.features.q1_corpus
  nice -n 19 $PY -m src.features.player_style_features --corpus q1_challenger

##q1_challenger --> partidas do corpus de clusterização
  nice -n 19 $PY -m src.export --table player_style_features \
    --out ~/exports/player_style_features_q1.tmp.parquet \
    --where "match_id IN (SELECT match_id FROM corpus_membership WHERE corpus = 'q1_challenger')" \
    && mv ~/exports/player_style_features_q1.tmp.parquet ~/exports/player_style_features_q1.parquet
  psql "$DATABASE_URL" -c "\copy (SELECT puuid, region, tier, league_points, snapshot_at FROM cohort_challenger) TO '$HOME/exports/cohort_challenger.tmp.csv' WITH CSV HEADER" \
    && mv ~/exports/cohort_challenger.tmp.csv ~/exports/cohort_challenger.csv

  
else
  echo "Collect GM falhou."
fi
