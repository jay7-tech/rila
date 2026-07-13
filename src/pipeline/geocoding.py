import time
import requests
from typing import Optional, Tuple

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
            lat = float(data[0]["lat"])
            lon = float(data[0]["lon"])
            return lat, lon
    except Exception as e:
        print(f"Geocoding failed for '{query}': {e}")
        
    return None
