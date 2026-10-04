# features de estilo por (partida, jogador) da Q1: 10 linhas por partida, janelas _15, _late e _full
import math
import sys
from contextlib import nullcontext

import psycopg
from dotenv import load_dotenv

from src.db.store import get_conn
from src.features.common import MIN_15_MS, autofill, team_ids
from src.features.geometry import map_length, zone_for_role, distance, lane_zone, map_side

insert_batch = 2000
duracao_min = 600
read_chunk = 200
min_frames_late = 3      
lean_skip_ms = 180_000     

raio_obj = 2000         # "presente no objetivo" = dentro deste raio do evento
setup_lead_ms = 30_000    # obj_setup: frame de pelo menos 30 s antes do abate
raio_ally = 5000        
wards_validas = ("YELLOW_TRINKET", "BLUE_TRINKET", "SIGHT_WARD", "CONTROL_WARD")
elite = {"DRAGON": "dragon_td", "RIFTHERALD": "herald_td",
         "BARON_NASHOR": "baron_td", "HORDE": "horde_td"}


def extract_time(pid: int) -> int:
    if pid <= 5:
        return 100
    else:
        return 200


def participante_valido(pid: int) -> bool:
    return 1 <= pid <= 10


def assistentes(e: dict) -> list[int]:
    ids = []

    for a in e.get("assistingParticipantIds") or []:
        if participante_valido(a):
            ids.append(a)

    return ids


def lado_oposto(pos: dict, team: int) -> bool:
    s = pos.get("x", 0) + pos.get("y", 0)

    if team == 100:
        return s > map_length
    else:
        return s < map_length


def std_xy(positions: list[dict]) -> float:
    n = len(positions)

    if n < 2:
        return 0.0

    mx = 0
    my = 0

    for p in positions:
        mx += p["x"]
        my += p["y"]

    mx = mx / n
    my = my / n
    var = 0

    for p in positions:
        var += (p["x"] - mx) ** 2 + (p["y"] - my) ** 2

    return math.sqrt(var / n)


def rate(count: float, minutes: float):
    if minutes and minutes > 0:
        return round(count / minutes, 4)
    else:
        return None


def rate_opt(count, minutes: float):
    if count is None:
        return None
    else:
        return rate(count, minutes)


def share(part: float, total: float):
    if total and total > 0:
        return round(part / total, 4)
    else:
        return None


def kda(e: dict) -> float:
    return round((e["kills"] + e["assists"]) / max(e["deaths"], 1), 3)


#pos_spread, mean_dist_centroid e time_in_enemy_half por participante nos frames recebidos
def janela_espacial(frames: list[dict]) -> dict[int, dict]:
    per_frame = []

    for f in frames:
        pf = f.get("participantFrames", {})
        d = {}

        for pid in range(1, 11):
            pos = pf.get(str(pid), {}).get("position")

            if pos is not None:
                d[pid] = pos

        per_frame.append(d)

    out = {}

    for pid in range(1, 11):
        team = extract_time(pid)
        mates = team_ids(team)

        own = []

        for f in per_frame:
            if pid in f:
                own.append(f[pid])

        dists = []
        enemy_half = 0

        for f in per_frame:
            if pid not in f:
                continue

            if lado_oposto(f[pid], team):
                enemy_half += 1

            m = []

            for q in mates:
                if q != pid and q in f:
                    m.append(f[q])

            if m:
                cx = 0
                cy = 0

                for p in m:
                    cx += p["x"]
                    cy += p["y"]

                cx = cx / len(m)
                cy = cy / len(m)
                dists.append(distance(f[pid], {"x": cx, "y": cy}))

        if dists:
            mean_dist = round(sum(dists) / len(dists), 1)
        else:
            mean_dist = None

        if own:
            enemy_share = share(enemy_half, len(own))
        else:
            enemy_share = None

        out[pid] = {
            "pos_spread": round(std_xy(own), 1),
            "mean_dist_centroid": mean_dist,
            "time_in_enemy_half": enemy_share,
        }

    return out


#posição do participante no frame mais próximo do timestamp
def posicao_em(frames: list[dict], pid: int, ts: int):
    best = min(frames, key=lambda f: abs(f.get("timestamp", 0) - ts), default=None)

    if best is None:
        return None

    return best.get("participantFrames", {}).get(str(pid), {}).get("position")


#posição no último frame com timestamp <= ts - lead_ms
def posicao_antes(frames: list[dict], pid: int, ts: int, lead_ms: int):
    best = None

    for f in frames:
        if f.get("timestamp", 0) > ts - lead_ms:
            break

        best = f

    if best is None:
        return None

    return best.get("participantFrames", {}).get(str(pid), {}).get("position")


#side_solo: fração dos frames numa rota side  sem aliado a menos de uma distancia 

def janela_proximidade(frames: list[dict], radius: float = raio_ally) -> dict[int, dict]:
    acc = {}

    for pid in range(1, 11):
        acc[pid] = [0, 0, 0]   

    for f in frames:
        pf = f.get("participantFrames", {})
        pos = {}

        for pid in range(1, 11):
            pos[pid] = pf.get(str(pid), {}).get("position")

        for pid in range(1, 11):
            p = pos[pid]

            if not p:
                continue

            near = 0

            for q in team_ids(extract_time(pid)):
                if q != pid and pos[q] and distance(p, pos[q]) <= radius:
                    near += 1

            a = acc[pid]
            a[0] += 1
            a[2] += near

            if near == 0 and lane_zone(p) in ("TOP", "BOT"):
                a[1] += 1

    out = {}

    for pid, a in acc.items():
        if a[0]:
            allies_near = round(a[2] / a[0], 3)
        else:
            allies_near = None

        out[pid] = {"side_solo": share(a[1], a[0]), "allies_near": allies_near}

    return out


# (frames no lado do top - frames no lado do bot) / frames fora da faixa da mid
# +1 = sempre no top, -1 = sempre no bot
def preferencia_side(frames: list[dict], start_ms: int, end_ms: int) -> dict:
    top = {}
    bot = {}

    for pid in range(1, 11):
        top[pid] = 0
        bot[pid] = 0

    for f in frames:
        ts = f.get("timestamp", 0)

        if ts < start_ms or ts > end_ms:
            continue

        pf = f.get("participantFrames", {})

        for pid in range(1, 11):
            side = map_side(pf.get(str(pid), {}).get("position"))

            if side == "TOP":
                top[pid] += 1
            elif side == "BOT":
                bot[pid] += 1

    out = {}

    for pid in range(1, 11):
        total = top[pid] + bot[pid]

        if total:
            out[pid] = round((top[pid] - bot[pid]) / total, 3)
        else:
            out[pid] = None

    return out


# uma partida -> 10 linhas
def extract(match_id, mp, tp, cohort_map, region, patch=None, game_creation=None):
    info = mp.get("info", {})

    if not info or info.get("gameDuration", 0) < duracao_min:
        return None

    participantes = info.get("participants", [])

    if len(participantes) != 10:
        return None

    frames = tp.get("info", {}).get("frames", [])

    if not frames:
        return None

    frame15 = min(frames, key=lambda f: abs(f.get("timestamp", 0) - MIN_15_MS))
    ms15 = frame15.get("timestamp", 0)
    min15 = ms15 /60000.0
    frame_final = frames[-1]
    ms_full = frame_final.get("timestamp", 0)
    min_full = ms_full / 60000.0

    if not frame15.get("participantFrames") or min15 <= 0 or min_full <= 0:
        return None

    frames15 = []
    frames_late = []

    for f in frames:
        if f.get("timestamp", 0) <= ms15:
            frames15.append(f)
        else:
            frames_late.append(f)

    tem_late = len(frames_late) >= min_frames_late

    spat15 = janela_espacial(frames15)
    spat_full = janela_espacial(frames)

    if tem_late:
        spat_late = janela_espacial(frames_late)
        prox_late = janela_proximidade(frames_late)
    else:
        spat_late = None
        prox_late = None

    lean15 = preferencia_side(frames, lean_skip_ms, ms15)

    # contadores de eventos por janela
    def zero():
        return {"kills": 0, "deaths": 0, "assists": 0, "solo_kills": 0,
                "wards": 0, "control_wards": 0, "ward_kills": 0, "plates": 0,
                "turret_kills": 0, "turret_td": 0,
                "dragon_td": 0, "baron_td": 0, "herald_td": 0, "horde_td": 0,
                "obj_present": 0, "obj_total": 0,
                "setup_present": 0, "setup_total": 0,   # obj_setup (só monstros épicos)
                "ka_offzone": 0}   # kills+assists fora da lane

    eventos_15 = {}
    eventos_full = {}
    first_blood = {}
    first_tower = {}

    for pid in range(1, 11):
        eventos_15[pid] = zero()
        eventos_full[pid] = zero()
        first_blood[pid] = False
        first_tower[pid] = False


    role_zone = {}

    for p in participantes:
        role_zone[p.get("participantId")] = zone_for_role.get(p.get("teamPosition") or "")

    fb_done = False
    ft_done = False

    def somar(pid, key, until_15):
        if pid in eventos_full:
            eventos_full[pid][key] += 1

            if until_15:
                eventos_15[pid][key] += 1

    last_horde_ts = None

    for f in frames:
        for e in f.get("events", []):
            ts = e.get("timestamp", 0)
            until_15 = ts <= ms15
            et = e.get("type")
            count_obj = True   # False = mesmo acampamento de vastilarvas

            if et == "CHAMPION_KILL":
                k = e.get("killerId", 0)
                v = e.get("victimId", 0)
                assists = assistentes(e)

                if participante_valido(k):
                    somar(k, "kills", until_15)

                    if not assists:
                        somar(k, "solo_kills", until_15)

                if participante_valido(v):
                    somar(v, "deaths", until_15)

                for a in assists:
                    somar(a, "assists", until_15)

                #gank_roam: abate fora da zona de rota do jogador (só laners têm zona)
                kz = lane_zone(e.get("position"))

                if kz is not None:
                    if participante_valido(k) and role_zone.get(k) and kz != role_zone[k]:
                        somar(k, "ka_offzone", until_15)

                    for a in assists:
                        if role_zone.get(a) and kz != role_zone[a]:
                            somar(a, "ka_offzone", until_15)

                #first blood só conta se até os 15 min
                if not fb_done:
                    fb_done = True

                    if until_15:
                        if participante_valido(k):
                            first_blood[k] = True

                        for a in assists:
                            first_blood[a] = True

            elif et == "WARD_PLACED":
                c = e.get("creatorId", 0)
                wt = e.get("wardType")

                if participante_valido(c) and wt in wards_validas:
                    somar(c, "wards", until_15)

                    if wt == "CONTROL_WARD":
                        somar(c, "control_wards", until_15)

            elif et == "WARD_KILL":
                k = e.get("killerId", 0)

                if participante_valido(k):
                    somar(k, "ward_kills", until_15)

            elif et == "TURRET_PLATE_DESTROYED":
                k = e.get("killerId", 0)

                if participante_valido(k):
                    somar(k, "plates", until_15)

            elif et == "BUILDING_KILL" and e.get("buildingType") == "TOWER_BUILDING":
                k = e.get("killerId", 0)
                assists = assistentes(e)

                if participante_valido(k):
                    somar(k, "turret_kills", until_15)

                for pid in ({k} | set(assists)):
                    if participante_valido(pid):
                        somar(pid, "turret_td", until_15)

                # first tower só conta se até os 15 min
                if not ft_done:
                    ft_done = True

                    if until_15:
                        if participante_valido(k):
                            first_tower[k] = True

                        for a in assists:
                            first_tower[a] = True

            elif et == "ELITE_MONSTER_KILL":
                mtype = e.get("monsterType", "")
                mkey = elite.get(mtype)
                k = e.get("killerId", 0)
                assists = assistentes(e)

                if mkey:
                    for pid in ({k} | set(assists)):
                        if participante_valido(pid):
                            somar(pid, mkey, until_15)

                if mtype == "HORDE":
                    # larvas a menos de 60 s uma da outra contam como um objetivo só
                    count_obj = last_horde_ts is None or ts - last_horde_ts > 60_000
                    last_horde_ts = ts

                # obj_setup: quem já estava no local 30-90 s antes do abate
                pos = e.get("position")

                if pos and count_obj:
                    for pid in range(1, 11):
                        ppos = posicao_antes(frames, pid, ts, setup_lead_ms)

                        if ppos is None:
                            continue

                        eventos_full[pid]["setup_total"] += 1

                        if distance(ppos, pos) <= raio_obj:
                            eventos_full[pid]["setup_present"] += 1

            # objective_presence: qualquer monstro épico + torres, presença por raio
            is_obj = (et == "ELITE_MONSTER_KILL" and count_obj) or \
                     (et == "BUILDING_KILL" and e.get("buildingType") == "TOWER_BUILDING")

            if is_obj:
                pos = e.get("position")

                for pid in range(1, 11):
                    eventos_full[pid]["obj_total"] += 1

                    if until_15:
                        eventos_15[pid]["obj_total"] += 1

                    if pos:
                        ppos = posicao_em(frames, pid, ts)

                        if ppos and distance(ppos, pos) <= raio_obj:
                            eventos_full[pid]["obj_present"] += 1

                            if until_15:
                                eventos_15[pid]["obj_present"] += 1

    # totais de time por janela (para os shares)
    def team_totals(frame, ev):
        g = {100: 0, 200: 0}
        xp = {100: 0, 200: 0}
        dmg = {100: 0, 200: 0}
        taken = {100: 0, 200: 0}
        tk = {100: 0, 200: 0}
        pf = frame.get("participantFrames", {})

        for pid in range(1, 11):
            fr = pf.get(str(pid), {})
            t = extract_time(pid)
            g[t] += fr.get("totalGold", 0)
            xp[t] += fr.get("xp", 0)
            dmg[t] += fr.get("damageStats", {}).get("totalDamageDoneToChampions", 0)
            taken[t] += fr.get("damageStats", {}).get("totalDamageTaken", 0)
            tk[t] += ev[pid]["kills"]

        return g, xp, dmg, taken, tk

    tg15, txp15, tdmg15, ttaken15, ttk15 = team_totals(frame15, eventos_15)
    tgF, txpF, tdmgF, ttakenF, ttkF = team_totals(frame_final, eventos_full)
    by_pid = {}

    for p in participantes:
        by_pid[p.get("participantId")] = p

    rows = []

    for pid in range(1, 11):
        p = by_pid.get(pid)

        if p is None:
            return None

        t = extract_time(pid)
        f15 = frame15["participantFrames"].get(str(pid), {})
        fF = frame_final["participantFrames"].get(str(pid), {})
        ds15 = f15.get("damageStats", {})
        dsF = fF.get("damageStats", {})
        e15 = eventos_15[pid]
        eF = eventos_full[pid]
        ch = p.get("challenges") or {}
        puuid = p.get("puuid")

        if role_zone.get(pid):
            gank_roam_15 = share(e15["ka_offzone"], e15["kills"] + e15["assists"])
            gank_roam_full = share(eF["ka_offzone"], eF["kills"] + eF["assists"])
        else:
            gank_roam_15 = None
            gank_roam_full = None

        if tem_late:
            late_kp = share(eF["kills"] + eF["assists"] - e15["kills"] - e15["assists"],
                            ttkF[t] - ttk15[t])
            late_spread = spat_late[pid]["pos_spread"]
            late_dist = spat_late[pid]["mean_dist_centroid"]
            late_half = spat_late[pid]["time_in_enemy_half"]
            late_solo = prox_late[pid]["side_solo"]
            late_perto = prox_late[pid]["allies_near"]
        else:
            late_kp = None
            late_spread = None
            late_dist = None
            late_half = None
            late_solo = None
            late_perto = None

        if ch.get("quickFirstTurret") is None:
            quick_first_turret = None
        else:
            quick_first_turret = float(ch["quickFirstTurret"])

        row = {
            "match_id": match_id,
            "participant_id": pid,
            "puuid": puuid,
            "champion_id": p.get("championId"),
            "team": t,
            "role": p.get("teamPosition") or None,
            "win": bool(p.get("win", False)),
            "region": region,
            "cohort_region": cohort_map.get(puuid),
            "patch": patch,
            "game_creation": game_creation,
            "autofill": autofill(p),
            "game_duration_min": round(info["gameDuration"] / 60.0, 2),
            "window_min_15": round(min15, 2),

            # duas janelas (timeline)
            "kills_15": rate(e15["kills"], min15),
            "kills_full": rate(eF["kills"], min_full),
            "deaths_15": rate(e15["deaths"], min15),
            "deaths_full": rate(eF["deaths"], min_full),
            "assists_15": rate(e15["assists"], min15),
            "assists_full": rate(eF["assists"], min_full),
            "kda_15": kda(e15),
            "kda_full": kda(eF),
            "kill_participation_15": share(e15["kills"] + e15["assists"], ttk15[t]),
            "kill_participation_full": share(eF["kills"] + eF["assists"], ttkF[t]),
            "solo_kills_15": rate(e15["solo_kills"], min15),
            "solo_kills_full": rate(eF["solo_kills"], min_full),
            "cs_per_min_15": rate(f15.get("minionsKilled", 0), min15),
            "cs_per_min_full": rate(fF.get("minionsKilled", 0), min_full),
            "jungle_cs_per_min_15": rate(f15.get("jungleMinionsKilled", 0), min15),
            "jungle_cs_per_min_full": rate(fF.get("jungleMinionsKilled", 0), min_full),
            "gold_per_min_15": rate(f15.get("totalGold", 0), min15),
            "gold_per_min_full": rate(fF.get("totalGold", 0), min_full),
            "xp_per_min_15": rate(f15.get("xp", 0), min15),
            "xp_per_min_full": rate(fF.get("xp", 0), min_full),
            "dmg_champ_per_min_15": rate(ds15.get("totalDamageDoneToChampions", 0), min15),
            "dmg_champ_per_min_full": rate(dsF.get("totalDamageDoneToChampions", 0), min_full),
            "dmg_share_15": share(ds15.get("totalDamageDoneToChampions", 0), tdmg15[t]),
            "dmg_share_full": share(dsF.get("totalDamageDoneToChampions", 0), tdmgF[t]),
            "dmg_taken_share_15": share(ds15.get("totalDamageTaken", 0), ttaken15[t]),
            "dmg_taken_share_full": share(dsF.get("totalDamageTaken", 0), ttakenF[t]),
            "wards_placed_per_min_15": rate(e15["wards"], min15),
            "wards_placed_per_min_full": rate(eF["wards"], min_full),
            "control_wards_per_min_15": rate(e15["control_wards"], min15),
            "control_wards_per_min_full": rate(eF["control_wards"], min_full),
            "ward_kills_per_min_15": rate(e15["ward_kills"], min15),
            "ward_kills_per_min_full": rate(eF["ward_kills"], min_full),
            "turret_kills_15": rate(e15["turret_kills"], min15),
            "turret_kills_full": rate(eF["turret_kills"], min_full),
            "turret_takedowns_15": rate(e15["turret_td"], min15),
            "turret_takedowns_full": rate(eF["turret_td"], min_full),
            "dragon_takedowns_15": rate(e15["dragon_td"], min15),
            "dragon_takedowns_full": rate(eF["dragon_td"], min_full),
            "baron_takedowns_15": rate(e15["baron_td"], min15),
            "baron_takedowns_full": rate(eF["baron_td"], min_full),
            "herald_takedowns_15": rate(e15["herald_td"], min15),
            "herald_takedowns_full": rate(eF["herald_td"], min_full),
            "horde_takedowns_15": rate(e15["horde_td"], min15),
            "horde_takedowns_full": rate(eF["horde_td"], min_full),
            "pos_spread_15": spat15[pid]["pos_spread"],
            "pos_spread_full": spat_full[pid]["pos_spread"],
            "mean_dist_centroid_15": spat15[pid]["mean_dist_centroid"],
            "mean_dist_centroid_full": spat_full[pid]["mean_dist_centroid"],
            "time_in_enemy_half_15": spat15[pid]["time_in_enemy_half"],
            "time_in_enemy_half_full": spat_full[pid]["time_in_enemy_half"],
            "objective_presence_15": share(e15["obj_present"], e15["obj_total"]),
            "objective_presence_full": share(eF["obj_present"], eF["obj_total"]),
            #gank_roam: share dos abates+assist match_features.py 
            "gank_roam_15": gank_roam_15,
            "gank_roam_full": gank_roam_full,

            # janela _late (frames depois dos 15 min)
            "kill_participation_late": late_kp,
            "pos_spread_late": late_spread,
            "mean_dist_centroid_late": late_dist,
            "time_in_enemy_half_late": late_half,
            "side_solo_late": late_solo,
            "allies_near_late": late_perto,

            "first_blood_15": first_blood[pid],
            "first_tower_15": first_tower[pid],
            "plates_15": rate(e15["plates"], min15),
            "side_lean_15": lean15[pid],

            "vision_score_per_min_full": rate(p.get("visionScore", 0), min_full),
            "enemy_jungle_cs_full": rate(p.get("totalEnemyJungleMinionsKilled", 0), min_full),
            "ally_jungle_cs_full": rate(p.get("totalAllyJungleMinionsKilled", 0), min_full),
            "dmg_to_turrets_per_min_full": rate(p.get("damageDealtToTurrets", 0), min_full),
            "dmg_to_objectives_per_min_full": rate(p.get("damageDealtToObjectives", 0), min_full),
            "obj_setup_full": share(eF["setup_present"], eF["setup_total"]),
            "scuttle_kills_full": rate_opt(ch.get("scuttleCrabKills"), min_full),
            "epic_monster_steals_full": rate_opt(ch.get("epicMonsterSteals"), min_full),
            "initial_crab_full": ch.get("initialCrabCount"),
            "quick_first_turret_full": quick_first_turret,
            "control_ward_coverage_full": ch.get("controlWardTimeCoverageInRiverOrEnemyHalf"),
        }
        rows.append(row)

    return rows


# colunas derivadas da 1ª linha; tipos inferidos
key_cols = [("match_id", "TEXT"), ("participant_id", "INT")]
ctx_cols = [("puuid", "TEXT"), ("champion_id", "INT"), ("team", "INT"), ("role", "TEXT"),
            ("win", "BOOLEAN"), ("region", "TEXT"), ("cohort_region", "TEXT"),
            ("patch", "TEXT"), ("game_creation", "TIMESTAMPTZ"), ("autofill", "BOOLEAN"),
            ("game_duration_min", "REAL"), ("window_min_15", "REAL")]
bool_cols = {"first_blood_15", "first_tower_15", "win"}


def feature_columns(sample_row: dict) -> list[tuple[str, str]]:
    known = set()

    for c, _ in key_cols + ctx_cols:
        known.add(c)

    cols = []

    for k in sample_row:
        if k in known:
            continue

        if k in bool_cols:
            cols.append((k, "BOOLEAN"))
        else:
            cols.append((k, "REAL"))

    return cols


# tabela + colunas de contexto, criadas antes do pending_ids (que faz LEFT JOIN nela)
def criar_base(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS player_style_features (
                match_id       TEXT NOT NULL REFERENCES matches_raw (match_id),
                participant_id INT  NOT NULL,
                computed_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (match_id, participant_id)
            )
            """
        )

        for col, dtype in ctx_cols:
            cur.execute(f"ALTER TABLE player_style_features ADD COLUMN IF NOT EXISTS {col} {dtype}")

        cur.execute("CREATE INDEX IF NOT EXISTS psf_puuid_role_idx ON player_style_features (puuid, role)")
        cur.execute("CREATE INDEX IF NOT EXISTS psf_region_idx ON player_style_features (region)")
        cur.execute("CREATE INDEX IF NOT EXISTS psf_cohort_idx ON player_style_features (cohort_region)")

    conn.commit()


def criar_features(conn, feature_cols) -> None:
    with conn.cursor() as cur:
        for col, dtype in feature_cols:
            cur.execute(f"ALTER TABLE player_style_features ADD COLUMN IF NOT EXISTS {col} {dtype}")

    conn.commit()


def read_cohort_challenger(conn) -> dict[str, str]:
    cohort = {}

    with conn.cursor() as cur:
        try:
            cur.execute("SELECT DISTINCT puuid, region FROM cohort_challenger")

            for r in cur.fetchall():
                cohort[r["puuid"]] = r["region"]
        except psycopg.errors.UndefinedTable:
            conn.rollback()

    return cohort


# since: só partidas multi-região (region não nula) com game_creation >= since, para não
# reprocessar o backlog BR velho; corpus: restringe ao manifesto corpus_membership
def pending_ids(conn, rebuild, since=None, corpus=None) -> list[str]:
    conds = []
    params = {"since": since, "corpus": corpus}
    corpus_join = ""

    if corpus:
        corpus_join = ("JOIN corpus_membership cm "
                       "ON cm.match_id = m.match_id AND cm.corpus = %(corpus)s")

    if not rebuild:
        conds.append("f.match_id IS NULL")

    if since:
        conds.append("m.region IS NOT NULL")
        conds.append("m.game_creation >= %(since)s")

    if conds:
        where = "WHERE " + " AND ".join(conds)
    else:
        where = ""

    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT m.match_id
            FROM matches_raw m
            JOIN match_timelines_raw t ON t.match_id = m.match_id
            {corpus_join}
            LEFT JOIN (SELECT DISTINCT match_id FROM player_style_features) f ON f.match_id = m.match_id
            {where}
            ORDER BY m.match_id
            """,
            params,
        )
        ids = []

        for r in cur.fetchall():
            ids.append(r["match_id"])

        return ids


def iter_pending(conn, match_ids):
    for i in range(0, len(match_ids), read_chunk):
        chunk = match_ids[i:i + read_chunk]

        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT m.match_id, m.region, m.patch, m.game_creation,
                       m.payload AS mp, t.payload AS tp
                FROM matches_raw m JOIN match_timelines_raw t ON t.match_id = m.match_id
                WHERE m.match_id = ANY(%s)
                """,
                (chunk,),
            )

            for r in cur.fetchall():
                yield r


def flush(rows, cols, conn):
    allcols = []

    for c, _ in key_cols + ctx_cols + cols:
        allcols.append(c)

    values = []
    updates = []

    for c in allcols:
        values.append(f"%({c})s")

        if c not in ("match_id", "participant_id"):
            updates.append(f"{c}=EXCLUDED.{c}")

    names = ", ".join(allcols)
    values = ", ".join(values)
    updates = ", ".join(updates)

    params = []

    for r in rows:
        linha = {}

        for c in allcols:
            linha[c] = r.get(c)

        params.append(linha)

    with conn.cursor() as cur:
        cur.executemany(
            f"INSERT INTO player_style_features ({names}) VALUES ({values}) "
            f"ON CONFLICT (match_id, participant_id) DO UPDATE SET {updates}, computed_at=now()",
            params,
        )

    conn.commit()


def recalcular(rebuild=False, conn=None, since=None, corpus=None):
    if conn is None:
        ctx = get_conn()
    else:
        ctx = nullcontext(conn)

    with ctx as c:
        cohort_map = read_cohort_challenger(c)
        criar_base(c)
        match_ids = pending_ids(c, rebuild, since, corpus)

        feature_cols = None
        batch = []
        done = 0
        skipped = 0

        for r in iter_pending(c, match_ids):
            rows = extract(r["match_id"], r["mp"], r["tp"], cohort_map, r["region"],
                           patch=r["patch"], game_creation=r["game_creation"])

            if not rows:
                skipped += 1
                continue

            if feature_cols is None:
                feature_cols = feature_columns(rows[0])
                criar_features(c, feature_cols)

            batch.extend(rows)

            if len(batch) >= insert_batch:
                flush(batch, feature_cols, c)
                done += len(batch)
                batch = []

        if batch and feature_cols is not None:
            flush(batch, feature_cols, c)
            done += len(batch)

        print(f"player_style_features concluido")


if __name__ == "__main__":
    load_dotenv()

    rebuild = "--rebuild" in sys.argv

    since = None

    if "--since" in sys.argv:
        since = sys.argv[sys.argv.index("--since") + 1]

    corpus = None

    if "--corpus" in sys.argv:
        corpus = sys.argv[sys.argv.index("--corpus") + 1]

    recalcular(rebuild=rebuild, since=since, corpus=corpus)
