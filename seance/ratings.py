"""Classifications (FR, US, …) -> âge minimal, pour le contrôle parental."""
import re

AGES = {
    "u": 0, "tp": 0, "tous publics": 0, "tout public": 0, "g": 0, "tv-y": 0, "tv-g": 0, "all": 0, "al": 0, "0": 0,
    "tv-y7": 7, "tv-y7-fv": 7, "pg": 10, "tv-pg": 10, "pg-13": 13, "tv-14": 14, "r": 17, "tv-ma": 17,
    "nc-17": 18, "x": 18, "-10": 10, "-12": 12, "-16": 16, "-18": 18,
}


def age_of(rating):
    """« 12 », « FR-16 », « PG-13 », « TV-MA », « FSK 12 »… -> âge, ou None si inconnu."""
    if not rating:
        return None
    r = str(rating).strip().lower()
    if r in AGES:
        return AGES[r]
    m = re.match(r"^(?:[a-z]{2}[-/:])?(?:fsk|bbfc|csa|kijkwijzer|age)?\s*[-+]?\s*(\d{1,2})\s*\+?(?:\s*ans)?$", r)
    if m:
        return int(m.group(1))
    return None
