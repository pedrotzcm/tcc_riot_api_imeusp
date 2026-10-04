from __future__ import annotations

import math

map_length = 14870  # lado do mapa (quadrado)
sqrt2 = 2 ** 0.5

w_mid = 2000
w_side = 2900  # maior: começo do rio ainda conta como lane

zone_for_role = {"TOP": "TOP", "MIDDLE": "MID", "BOTTOM": "BOT", "UTILITY": "BOT"}  # sem jungle


def lane_zone(pos, mid=w_mid, side=w_side):
  
    if not pos:
        return None

    x, y = pos.get("x"), pos.get("y")
    if x is None or y is None:
        return None

    if abs(x - y) / sqrt2 < mid:
        return "MID"

    if y > x and min(x, map_length - y) < side:
        return "TOP"

    if x > y and min(y, map_length - x) < side:
        return "BOT"

    return "JUNGLE"


def map_side(pos, mid=w_mid):
    # mid é none
    if not pos:
        return None

    x, y = pos.get("x"), pos.get("y")
    if x is None or y is None:
        return None

    if abs(x - y) / sqrt2 < mid:
        return None

    if y > x:
        return "TOP"

    return "BOT"


def distance(a, b):
    # dist euclidiana x e y
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])
