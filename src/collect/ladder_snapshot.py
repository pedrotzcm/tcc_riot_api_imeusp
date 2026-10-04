from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

from src.db import store
from src.riot import discover

load_dotenv()

DIVISIONS = ("I", "II", "III", "IV")


def main() -> None:
    flags = set(sys.argv[1:])
    if flags - {"--discover"}:
        raise SystemExit(f"flags desconhecidas: {sorted(flags - {'--discover'})}")

    raw = os.environ.get("LADDER_DIAMOND", "I,II,III,IV")
    divisions = []
    bad = []
    for part in raw.split(","):
        division = part.strip().upper()

        if not division:
            continue
        divisions.append(division)
        if division not in DIVISIONS:
            bad.append(division)

    divisions = tuple(divisions)
    if bad:
        raise SystemExit(f"erros: {bad}")

    with store.get_conn() as conn:
        if "--discover" in flags:
            discover.refresh_high_elo(conn, divisions)
        else:
            discover.snapshot_ladder(conn, divisions, source="hourly")


if __name__ == "__main__":
    main()
