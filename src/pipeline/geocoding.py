import time
import requests
from typing import Optional, Tuple
from rapidfuzz import fuzz

def geocode_place(place_name: str, city: str) -> Optional[Tuple[float, float]]:
    """
    Uses Nominatim to geocode a place_name and city.
    Respects the 1 request/second rate limit by sleeping before the request.
    Returns (lat, lon) or None if not found.
    """
    if not place_name or not city:
        return None
        
    # Sleep to respect 1 req/sec limit
    time.sleep(1.1)
    
    query = f"{place_name}, {city}"
    url = "https://nominatim.openstreetmap.org/search"
    headers = {
        "User-Agent": "RILA-Bot/1.0 (jay7-tech/rila)"
    }
    params = {
        "q": query,
        "format": "json",
        "limit": 1
    }
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        
        if data and len(data) > 0:
            result = data[0]
            display_name = result.get("display_name", "")
            
            # Fuzzy match place_name against display_name
            # token_set_ratio is good for subset matching
            score = fuzz.token_set_ratio(place_name.lower(), display_name.lower())
            
            if score < 60:
                print(f"  [Geocode] Rejected '{place_name}' -> '{display_name}' (Score: {score} < 60)")
                return None
                
            print(f"  [Geocode] Accepted '{place_name}' -> '{display_name}' (Score: {score})")
            
            lat = float(result["lat"])
            lon = float(result["lon"])
            return lat, lon
    except Exception as e:
        print(f"Geocoding failed for '{query}': {e}")
        
    return None
