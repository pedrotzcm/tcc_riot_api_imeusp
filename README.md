# tcc_riot_api_imeusp

Estudo de partidas do high-elo da SoloQ (Riot API): arquétipos de estilo de jogo (Q1)
e preditores de vitória aos 15 minutos (Q2). TCC, IME-USP.

## Escopo deste repositório
Coleta (Match-V5, League-V4, Champion-Mastery-V4, Data Dragon) e engenharia de features.
A camada de acesso ao banco (`src/db/store.py`) **não está incluída**, então o código de
coleta não roda sem ela. O schema está em `src/db/schema.sql`.

## Setup
```bash
docker compose up -d
pip install -r requirements.txt
cp .env.example .env      # preencher RIOT_API_KEY
python -m src.db.setup
```

## Coleta
| Script | O que faz | Agendamento |
|---|---|---|
| `coleta_gm.sh` | Q1: Challenger + top100 GM, features de estilo | semanal |
| `coleta_q2_lp.sh` | Q2: enfileira, baixa, filtra, features pré-jogo | ter/sáb |
| `ladder_snapshot.sh` | foto da ladder BR1 | de hora em hora |
| `mastery_photo.sh` | fatia 1/24 da maestria do Master+ | de hora em hora |

Arquitetura: ver `ARQUITETURA.md`.
