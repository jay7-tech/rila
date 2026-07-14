import time
import requests
from typing import Optional, Tuple
from rapidfuzz import fuzz

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "RILA-Bot/1.0 (jay7-tech/rila)"
MIN_MATCH_SCORE = 65  # stricter now that we compare name-to-name, not name-to-address


def geocode_place(place_name: str, city: str) -> Optional[Tuple[float, float]]:
    """
    Geocodes place_name + city via Nominatim, with a fuzzy-match sanity check
    to reject false-positive matches caused by ASR mis-transcription (e.g.
    Whisper mishearing "Katz's Delicatessen" as "Cats", which previously
    matched "Dapper Cats Barber Lounge" with a token_set_ratio of 100).

    Requests the top 3 candidates and scores each against ONLY the first
    comma-segment of its display_name (the actual venue name, not the full
    padded address), picking the best-scoring candidate above threshold.

    Returns (lat, lon) on a confident match, or None if not found or rejected.
    """
    if not place_name or not city:
        return None

    time.sleep(1.1)  # respect Nominatim's 1 req/sec rate limit

    query = f"{place_name}, {city}"
    params = {"q": query, "format": "json", "limit": 3}
    headers = {"User-Agent": USER_AGENT}

    try:
        response = requests.get(NOMINATIM_URL, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        results = response.json()
    except Exception as e:
        print(f"  [Geocode] Request failed for '{query}': {e}")
        return None

    if not results:
        print(f"  [Geocode] No results for '{query}'")
        return None

    best_match = None
    best_score = 0.0
    best_primary_name = ""

    for result in results:
        display_name = result.get("display_name", "")
        primary_name = display_name.split(",")[0].strip()
        score = fuzz.ratio(place_name.lower(), primary_name.lower())

        if score > best_score:
            best_score = score
            best_match = result
            best_primary_name = primary_name

    if best_score < MIN_MATCH_SCORE:
        print(f"  [Geocode] Rejected '{place_name}' -> best candidate '{best_primary_name}' (score {best_score:.1f} < {MIN_MATCH_SCORE})")
        return None

    print(f"  [Geocode] Accepted '{place_name}' -> '{best_primary_name}' (score {best_score:.1f})")
    lat = float(best_match["lat"])
    lon = float(best_match["lon"])
    return lat, lon
