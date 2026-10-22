import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.db.database import SessionLocal, Place
from sqlalchemy import func

async def test_nearby(lat, lon, label):
    db = SessionLocal()
    try:
        point_wkt = f"SRID=4326;POINT({lon} {lat})"
        results = db.query(
            Place,
            func.ST_Distance(Place.location, point_wkt).label("distance"),
            func.ST_AsText(Place.location).label("wkt")
        ).filter(
            Place.location.is_not(None)
        ).order_by("distance").limit(5).all()
        
        print(f"\n======================================")
        print(f"Testing Nearby from: {label} ({lat}, {lon})")
        print(f"======================================")
        
        if not results:
            print("No saved places with known locations found.")
            return
            
        closest_dist = results[0][1]
        if closest_dist > 5000:
            reply_lines = ["Nothing nearby, but here are your closest saved places for reference:"]
        else:
            reply_lines = ["Nearest saved places:"]
            
        for place, dist, wkt in results:
            dist_text = f"{dist:.0f}m away" if dist < 1000 else f"{(dist/1000):.1f}km away"
            marker = "📍 " if dist <= 2000 else ""
            link = "no pin available"
            if wkt:
                lon_lat = wkt.replace('POINT(', '').replace(')', '').split()
                if len(lon_lat) == 2:
                    p_lon, p_lat = lon_lat
                    link = f"https://www.google.com/maps?q={p_lat},{p_lon}"
                    
            reply_lines.append(f"{marker}- {place.place_name} ({place.city}, {place.category}) - {dist_text}\n  {link}")
            
        print("\n".join(reply_lines))
    finally:
        db.close()

if __name__ == "__main__":
    # Near Plente in Indiranagar (approx 12.971, 77.647) - Distance should be very small
    asyncio.run(test_nearby(12.971, 77.647, "Indiranagar (Near Plente)"))
    
    # Far from everything (New York, NY - approx 40.71, -74.00)
    asyncio.run(test_nearby(40.71, -74.00, "New York (Far away from everything)"))
