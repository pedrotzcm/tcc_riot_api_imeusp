# features da Q2 (primeiros 15 min) a partir do timeline -> match_features
import sys
from contextlib import nullcontext

import psycopg
from dotenv import load_dotenv

from src.db.store import get_conn
from src.features.common import MIN_15_MS, autofill, team_ids
from src.features.geometry import distance

insert_batch = 200
fetch_chunk = 500 
# abaixo de 15 min é remake ou AFK e a janela das features não existe por inteiro
min_duration_s = 900


def extract_time(participant_id: int) -> int:
    # participantes 1-5 são o time azul (100)
    if participant_id <= 5:
        return 100
    else:
        return 200


def time_oposto(team: int) -> int:
    if team == 100:
        return 200
    else:
        return 100


def patch_partida(game_version: str) -> str:
    parts = game_version.split(".")
    return f"{parts[0]}.{parts[1]}"


def extract(
    match_id: str,
    match_payload: dict,
    timeline_payload: dict,
    champ_scores: dict[int, dict],
    roster: set[str],
):
    info = match_payload.get("info", {})
    if not info:
        return None

    if info.get("gameDuration", 0) < min_duration_s:
        return None

    frames = timeline_payload.get("info", {}).get("frames", [])
    if not frames:
        return None

    teams = {t["teamId"]: t for t in info.get("teams", [])}
    team_100_win = teams.get(100, {}).get("win", False)

    # frame mais próximo de 15 min
    frame_15 = None
    for frame in frames:
        if frame.get("timestamp", 0) <= MIN_15_MS:
            frame_15 = frame
        else:
            break

    if frame_15 is None:
        return None

    pf = frame_15.get("participantFrames", {})

    def soma_times(stat: str, team: int) -> int:
        total = 0
        for i in team_ids(team):
            total += pf.get(str(i), {}).get(stat, 0)
        return total

    def nivel_medio_time(team: int) -> float:
        return soma_times("level", team) / 5

    # features agregadas do time
    gold_t100 = soma_times("totalGold", 100)
    gold_t200 = soma_times("totalGold", 200)
    xp_t100 = soma_times("xp", 100)
    xp_t200 = soma_times("xp", 200)
    cs_t100 = soma_times("minionsKilled", 100)
    cs_t200 = soma_times("minionsKilled", 200)
    jcs_t100 = soma_times("jungleMinionsKilled", 100)
    jcs_t200 = soma_times("jungleMinionsKilled", 200)

    # eventos até 15 min (o frame N guarda os eventos entre N-1 e N)
    all_events = []
    for frame in frames:
        for event in frame.get("events", []):
            if event.get("timestamp", 0) <= MIN_15_MS:
                all_events.append(event)

    # None: o evento pode não ocorrer; 0: ocorreu zero vezes
    first_blood_team = None
    first_blood_minute = None
    kills_t100 = 0
    kills_t200 = 0
    kills_by_participant = {}

    dragons = []  # vários eventos

    first_herald_team = None
    first_herald_minute = None

    voidgrubs_t100 = 0
    voidgrubs_t200 = 0

    first_tower_team = None
    first_tower_minute = None

    towers_t100 = 0
    towers_t200 = 0
    plates_t100 = 0
    plates_t200 = 0
    wards_t100 = 0
    wards_t200 = 0
    control_wards_t100 = 0
    control_wards_t200 = 0
    ward_kills_t100 = 0
    ward_kills_t200 = 0

    for event in all_events:
        etype = event.get("type")
        ts_min = round(event.get("timestamp", 0) / 60000, 2)

        if etype == "CHAMPION_KILL":
            killer_id = event.get("killerId", 0)

            if killer_id == 0:
                continue  # morreu por torre/minion
            team = extract_time(killer_id)
            if team == 100:
                kills_t100 += 1
            else:
                kills_t200 += 1
            kills_by_participant[killer_id] = kills_by_participant.get(killer_id, 0) + 1
            if first_blood_team is None:
                first_blood_team = team
                first_blood_minute = ts_min

        elif etype == "ELITE_MONSTER_KILL":
            monster = event.get("monsterType", "")
            killer_id = event.get("killerId", 0)
            if killer_id > 0:
                team = extract_time(killer_id)
            else:
                team = None

            # 2 é o máximo de dragões até 15 min
            if monster == "DRAGON" and len(dragons) < 2:
                subtype = event.get("monsterSubType", "UNKNOWN").replace("_DRAGON", "")
                dragons.append({"team": team, "type": subtype, "minute": ts_min})

            elif monster == "RIFTHERALD" and first_herald_team is None:
                first_herald_team = team
                first_herald_minute = ts_min

            elif monster == "HORDE" and team is not None:  # voidgrubs
                if team == 100:
                    voidgrubs_t100 += 1
                else:
                    voidgrubs_t200 += 1

        elif etype == "BUILDING_KILL":
            if event.get("buildingType") == "TOWER_BUILDING":
                derrubou_torre_team = time_oposto(event.get("teamId"))

                if first_tower_team is None:
                    first_tower_team = derrubou_torre_team
                    first_tower_minute = ts_min

                if derrubou_torre_team == 100:
                    towers_t100 += 1
                else:
                    towers_t200 += 1

        elif etype == "TURRET_PLATE_DESTROYED":
            derrubou_placa_team = time_oposto(event.get("teamId"))
            if derrubou_placa_team == 100:
                plates_t100 += 1
            else:
                plates_t200 += 1

        elif etype == "WARD_PLACED":
            creator_id = event.get("creatorId", 0)
            if creator_id > 0:
                team = extract_time(creator_id)
                ward_type = event.get("wardType")
                
                ward_real = ward_type in ("YELLOW_TRINKET", "BLUE_TRINKET",
                                          "SIGHT_WARD", "CONTROL_WARD") #tratametno contra wards do teemo, zyra, etc.
                ward_control = ward_type == "CONTROL_WARD"
                if team == 100:
                    if ward_real:
                        wards_t100 += 1
                    if ward_control:
                        control_wards_t100 += 1
                else:
                    if ward_real:
                        wards_t200 += 1
                    if ward_control:
                        control_wards_t200 += 1

        elif etype == "WARD_KILL":
            killer_id = event.get("killerId", 0)
            if killer_id > 0:
                if extract_time(killer_id) == 100:
                    ward_kills_t100 += 1
                else:
                    ward_kills_t200 += 1

    def kill_concentration(team: int) -> float:
        team_kills = []
        for i in team_ids(team):
            team_kills.append(kills_by_participant.get(i, 0))
        total = sum(team_kills)
        if total > 0:
            return round(max(team_kills) / total, 3)
        else:
            return 0.0

    attack = {100: 0, 200: 0}
    magic = {100: 0, 200: 0}
    scores_ok = True

    #mix de dano: soma attack e magic dos 5 campeões de cada time e normaliza
    for p in info.get("participants", []):
        scores = champ_scores.get(p.get("championId"))
        if scores is None:
            scores_ok = False  # campeão sem score: o dado fica incompleto
        else:
            team = p.get("teamId")
            attack[team] += scores["attack"]
            magic[team] += scores["magic"]

    def phys_ratio(team: int):
        total = attack[team] + magic[team]
        if scores_ok and total > 0:
            return round(attack[team] / total, 4)
        else:
            return None

    #proximidade do jungler aos laners (top/bot): distância média do mesmo time nos frames ≤15 min
    role_pid = {100: {}, 200: {}}  # time -> {rota: participantId}
    autofills = {100: None, 200: None}

    for p in info.get("participants", []):
        team = p.get("teamId")

        position = p.get("teamPosition")
        if position:
            role_pid[team][position] = p.get("participantId")

        is_autofill = autofill(p)
        if is_autofill is not None:
            count = autofills[team]
            if count is None:
                count = 0
            if is_autofill:
                autofills[team] = count + 1
            else:
                autofills[team] = count

    def participant_pos(pf: dict, pid: int):
        if pid:
            return pf.get(str(pid), {}).get("position")
        else:
            return None

    jg_dist = {
        100: {"TOP": [], "BOTTOM": []},
        200: {"TOP": [], "BOTTOM": []},
    }
    for frame in frames:
        if frame.get("timestamp", 0) > MIN_15_MS:
            break
        fpf = frame.get("participantFrames", {})
        for team in (100, 200):
            jg = participant_pos(fpf, role_pid[team].get("JUNGLE"))
            if jg is None:
                continue
            for lane in ("TOP", "BOTTOM"):
                lane_pos = participant_pos(fpf, role_pid[team].get(lane))
                if lane_pos is not None:
                    jg_dist[team][lane].append(distance(jg, lane_pos))

    def jg_dist_avg(team: int, lane: str):
        vals = jg_dist[team][lane]
        if vals:
            return round(sum(vals) / len(vals), 1)
        else:
            return None

    #diff de ouro por rota (t100 - t200)
    def lane_gold_diff(role: str):
        a = role_pid[100].get(role)
        b = role_pid[200].get(role)
        if not a or not b:
            return None
        ga = pf.get(str(a), {}).get("totalGold", 0)
        gb = pf.get(str(b), {}).get("totalGold", 0)
        return ga - gb

    # cobertura de players high-elo
    n_roster_high_elo = 0
    for puuid in match_payload.get("metadata", {}).get("participants", []):
        if puuid in roster:
            n_roster_high_elo += 1

    d1 = {}
    d2 = {}
    if len(dragons) > 0:
        d1 = dragons[0]
    if len(dragons) > 1:
        d2 = dragons[1]

    return {
        "match_id": match_id,
        "patch": patch_partida(info["gameVersion"]),
        "duration_min": round(info["gameDuration"] / 60, 2),
        "team_100_win": team_100_win,  # target

        # diferenciais de frame
        "gold_diff_15": gold_t100 - gold_t200,
        "xp_diff_15": xp_t100 - xp_t200,
        "cs_diff_15": cs_t100 - cs_t200,
        "jungle_cs_diff_15": jcs_t100 - jcs_t200,
        "avg_level_diff_15": round(nivel_medio_time(100) - nivel_medio_time(200), 2),

        # kills
        "first_blood_team": first_blood_team,
        "first_blood_minute": first_blood_minute,
        "kills_t100": kills_t100,
        "kills_t200": kills_t200,
        "kill_concentration_t100": kill_concentration(100),
        "kill_concentration_t200": kill_concentration(200),

        # visão
        "wards_placed_t100": wards_t100,
        "wards_placed_t200": wards_t200,
        "control_wards_t100": control_wards_t100,
        "control_wards_t200": control_wards_t200,
        "ward_kills_t100": ward_kills_t100,
        "ward_kills_t200": ward_kills_t200,

        # dragões
        "dragon_1_team": d1.get("team"),
        "dragon_1_type": d1.get("type"),
        "dragon_1_minute": d1.get("minute"),
        "dragon_2_team": d2.get("team"),
        "dragon_2_type": d2.get("type"),
        "dragon_2_minute": d2.get("minute"),

        # herald
        "first_herald_team": first_herald_team,
        "first_herald_minute": first_herald_minute,

        # estruturas
        "towers_t100": towers_t100,
        "towers_t200": towers_t200,
        "plates_t100": plates_t100,
        "plates_t200": plates_t200,

        # mix de dano (proxy de draft)
        "phys_dmg_ratio_t100": phys_ratio(100),
        "phys_dmg_ratio_t200": phys_ratio(200),

        # proximidade jungler-laner (menor = mais foco na lane)
        "jg_dist_top_t100": jg_dist_avg(100, "TOP"),
        "jg_dist_bot_t100": jg_dist_avg(100, "BOTTOM"),
        "jg_dist_top_t200": jg_dist_avg(200, "TOP"),
        "jg_dist_bot_t200": jg_dist_avg(200, "BOTTOM"),

        # diff de ouro por lane aos 15 min
        "gold_diff_top_15": lane_gold_diff("TOP"),
        "gold_diff_jng_15": lane_gold_diff("JUNGLE"),
        "gold_diff_mid_15": lane_gold_diff("MIDDLE"),
        "gold_diff_bot_15": lane_gold_diff("BOTTOM"),
        "gold_diff_sup_15": lane_gold_diff("UTILITY"),

        # voidgrubs (HORDE) até 15 min
        "voidgrubs_t100": voidgrubs_t100,
        "voidgrubs_t200": voidgrubs_t200,

        # primeira torre do jogo
        "first_tower_team": first_tower_team,
        "first_tower_minute": first_tower_minute,

        # cobertura de roster high-elo (0-10; proxy de nível da lobby)
        "n_roster_high_elo": n_roster_high_elo,

        "autofills_t100": autofills[100],
        "autofills_t200": autofills[200],
    }


def migrate(conn: psycopg.Connection) -> None:
    new_columns = [
        ("gold_diff_15", "INT"),
        ("xp_diff_15", "INT"),
        ("cs_diff_15", "INT"),
        ("jungle_cs_diff_15", "INT"),
        ("avg_level_diff_15", "REAL"),
        ("first_blood_team", "INT"),
        ("first_blood_minute", "REAL"),
        ("kills_t100", "INT"),
        ("kills_t200", "INT"),
        ("kill_concentration_t100", "REAL"),
        ("kill_concentration_t200", "REAL"),
        ("wards_placed_t100", "INT"),
        ("wards_placed_t200", "INT"),
        ("control_wards_t100", "INT"),
        ("control_wards_t200", "INT"),
        ("ward_kills_t100", "INT"),
        ("ward_kills_t200", "INT"),
        ("dragon_1_team", "INT"),
        ("dragon_1_type", "TEXT"),
        ("dragon_1_minute", "REAL"),
        ("dragon_2_team", "INT"),
        ("dragon_2_type", "TEXT"),
        ("dragon_2_minute", "REAL"),
        ("first_herald_team", "INT"),
        ("first_herald_minute", "REAL"),
        ("towers_t100", "INT"),
        ("towers_t200", "INT"),
        ("plates_t100", "INT"),
        ("plates_t200", "INT"),
        ("phys_dmg_ratio_t100", "REAL"),
        ("phys_dmg_ratio_t200", "REAL"),
        ("jg_dist_top_t100", "REAL"),
        ("jg_dist_bot_t100", "REAL"),
        ("jg_dist_top_t200", "REAL"),
        ("jg_dist_bot_t200", "REAL"),
        ("gold_diff_top_15", "INT"),
        ("gold_diff_jng_15", "INT"),
        ("gold_diff_mid_15", "INT"),
        ("gold_diff_bot_15", "INT"),
        ("gold_diff_sup_15", "INT"),
        ("voidgrubs_t100", "INT"),
        ("voidgrubs_t200", "INT"),
        ("first_tower_team", "INT"),
        ("first_tower_minute", "REAL"),
        ("n_roster_high_elo", "INT"),
        ("autofills_t100", "INT"),
        ("autofills_t200", "INT"),
    ]
    with conn.cursor() as cur:
        for col, dtype in new_columns:
            cur.execute(
                f"ALTER TABLE match_features ADD COLUMN IF NOT EXISTS {col} {dtype}")
    conn.commit()
    print("Schema de match_features atualizado.")


def load_champion_scores(conn: psycopg.Connection) -> dict[int, dict]:
    scores = {}
    with conn.cursor() as cur:
        cur.execute("SELECT champion_id, attack, magic FROM champion_static")
        for r in cur.fetchall():
            scores[r["champion_id"]] = {"attack": r["attack"], "magic": r["magic"]}
    return scores


def load_roster(conn: psycopg.Connection) -> set[str]:
    roster = set()
    with conn.cursor() as cur:
        cur.execute("SELECT puuid FROM summoners_high_elo")
        for r in cur.fetchall():
            roster.add(r["puuid"])
    return roster


def get_pending_ids(conn: psycopg.Connection, rebuild=False, corpus=None) -> list[str]:
    # só os ids: os payloads vêm em chunks para não carregar todas as timelines na memória
    # corpus (ex.: 'q2_lp') restringe ao manifesto corpus_membership
    params = {}

    if corpus:
        corpus_join = ("JOIN corpus_membership cm "
                       "ON cm.match_id = m.match_id AND cm.corpus = %(corpus)s")
        params["corpus"] = corpus
    else:
        corpus_join = ""

    if rebuild:
        where = ""
    else:
        where = "WHERE f.match_id IS NULL"

    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT m.match_id
            FROM matches_raw m
            JOIN match_timelines_raw t ON t.match_id = m.match_id
            {corpus_join}
            LEFT JOIN match_features f ON f.match_id = m.match_id
            {where}
            """,
            params,
        )
        ids = []
        for row in cur.fetchall():
            ids.append(row["match_id"])
        return ids


def fetch_payloads(conn: psycopg.Connection, ids: list[str]) -> list:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT m.match_id, m.payload AS match_payload, t.payload AS timeline_payload
            FROM matches_raw m
            JOIN match_timelines_raw t ON t.match_id = m.match_id
            WHERE m.match_id = ANY(%s)
            """,
            (ids,),
        )
        return cur.fetchall()


def flush(rows: list[dict], conn: psycopg.Connection) -> None:
    cols = list(rows[0])  # as colunas são as chaves do dict; match_id vem primeiro
    values = []
    updates = []
    for c in cols:
        values.append(f"%({c})s")
        if c != "match_id":
            updates.append(f"{c} = EXCLUDED.{c}")
    names = ", ".join(cols)
    values = ", ".join(values)
    updates = ", ".join(updates)

    with conn.cursor() as cur:
        cur.executemany(
            f"INSERT INTO match_features ({names}) VALUES ({values}) "
            f"ON CONFLICT (match_id) DO UPDATE SET {updates}, computed_at = now()",
            rows,
        )
    conn.commit()


def run(conn=None, rebuild=False, corpus=None) -> None:
    if conn is None:
        ctx = get_conn()
    else:
        ctx = nullcontext(conn)

    with ctx as c:
        migrate(c)

        champ_scores = load_champion_scores(c)
        if not champ_scores:
            print("AVISO: champion_static vazia, phys_dmg_ratio ficará NULL. "
                  "Rode 'python -m src.datadragon' antes.")

        roster = load_roster(c)
        pending_ids = get_pending_ids(c, rebuild, corpus)
        print(f"match_features: {len(pending_ids)} partidas para processar")

        batch = []
        done = 0
        skipped = 0

        for i in range(0, len(pending_ids), fetch_chunk):
            for row in fetch_payloads(c, pending_ids[i:i + fetch_chunk]):
                out = extract(row["match_id"], row["match_payload"],
                              row["timeline_payload"], champ_scores, roster)
                if out:
                    batch.append(out)
                else:
                    skipped += 1

                if len(batch) >= insert_batch:
                    flush(batch, c)
                    done += len(batch)
                    batch = []
                    print(f"  {done} features inseridas...")

        if batch:
            flush(batch, c)
            done += len(batch)

        print(f"match_features concluído. {done} inseridas, {skipped} ignoradas.")


if __name__ == "__main__":
    load_dotenv()

    rebuild = "--rebuild" in sys.argv

    corpus = None
    if "--corpus" in sys.argv:
        corpus = sys.argv[sys.argv.index("--corpus") + 1]

    run(rebuild=rebuild, corpus=corpus)
