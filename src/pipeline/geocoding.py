import time
import requests
from typing import Optional, Tuple
from rapidfuzz import fuzz

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "RILA-Bot/1.0 (jay7-tech/rila)"
MIN_MATCH_SCORE = 65  # stricter now that we compare name-to-name, not name-to-address


def geocode_place(place_name: str, city: str) -> Tuple[Optional[float], Optional[float], str]:
    """
    Geocodes place_name + city via Nominatim, with a fuzzy-match sanity check
    to reject false-positive matches caused by ASR mis-transcription.

    Returns a 3-tuple: (lat, lon, status)
    status is one of:
      "confirmed"     - matched a real place above the confidence threshold
      "no_candidates" - Nominatim returned zero results (coverage gap, NOT
                         evidence the place doesn't exist — e.g. a small business
                         not in OSM yet)
      "rejected"      - Nominatim returned candidates but none matched well enough
                         (this IS the anti-hallucination signal — the LLM likely
                         invented or mis-transcribed a name that doesn't correspond
                         to a real nearby place)
      "error"         - the geocoding request itself failed (network/timeout)
    """
    if not place_name or not city:
        return None, None, "error"

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
        return None, None, "error"

    if not results:
        print(f"  [Geocode] No candidates for '{query}' (coverage gap, not rejecting)")
        return None, None, "no_candidates"

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
        return None, None, "rejected"

    print(f"  [Geocode] Accepted '{place_name}' -> '{best_primary_name}' (score {best_score:.1f})")
    lat = float(best_match["lat"])
    lon = float(best_match["lon"])
    return lat, lon, "confirmed"
