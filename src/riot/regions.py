from __future__ import annotations

# chave = código de plataforma em CAIXA ALTA, que também é o prefixo do match_id da região
REGIONS: dict[str, dict[str, str]] = {
    "BR1":  {"platform": "br1",  "cluster": "americas"},
    "KR":   {"platform": "kr",   "cluster": "asia"},
    "EUW1": {"platform": "euw1", "cluster": "europe"},
}


def platform_for(region: str) -> str:
    return REGIONS[region.upper()]["platform"]


def cluster_for(region: str) -> str:
    return REGIONS[region.upper()]["cluster"]


def region_of_match_id(match_id: str) -> str:
    # região ('BR1'|'KR'|'EUW1') a partir do prefixo do match_id
    prefix = match_id.split("_", 1)[0].upper()
    if prefix not in REGIONS:
        raise ValueError(f"prefixo de match_id desconhecido: {match_id!r}")

    return prefix
