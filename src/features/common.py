"""
Constantes e helpers compartilhados entre as features da Q1 (player_style_features) e
da Q2 (match_features).
"""
from __future__ import annotations

MIN_15_MS = 15 * 60 * 1000  # 900000 ms


def team_ids(team: int) -> range:
    """participantIds de um time: 1-5 = time 100 (azul), 6-10 = time 200 (vermelho)."""
    if team == 100:
        return range(1, 6)
    else:
        return range(6, 11)


def autofill(p: dict) -> bool | None:
   
    pref = p.get("selectedRolePreferences")
    if pref is None:
        return None
    parts = pref.split(".")
    if len(parts) >= 2 and parts[1] == "AUTOFILL":
        return True
    else:
        return False
