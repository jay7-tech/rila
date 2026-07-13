import os
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Ensure project root is in the python path so 'src' can be imported
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from src.pipeline.ingestion import process_reel
from src.pipeline.extraction import extract_place_info
from src.pipeline.geocoding import geocode_place

def run_test():
    test_urls = [
        "https://www.youtube.com/watch?v=zN5VfiDX_9Q", # NYC food
        "https://www.youtube.com/watch?v=kYv_3-D05Lg"  # Example food video
    ]

    for i, url in enumerate(test_urls, 1):
        print(f"\n{'='*50}")
        print(f"Testing URL {i}/{len(test_urls)}: {url}")
        print(f"{'='*50}")
        
        # 1. Ingestion
        print("\n--- PHASE 1: INGESTION ---")
        caption, transcript = process_reel(url)
        
        if caption is None and transcript is None:
            print("Ingestion failed. Skipping to next URL.")
            continue
            
        print(f"CAPTION:\n{caption[:200]}...")
        print(f"TRANSCRIPT:\n{transcript[:200]}...")
        
        # 2. Extraction
        print("\n--- PHASE 2: EXTRACTION ---")
        extracted_info = extract_place_info(caption, transcript)
        
        if extracted_info is None:
            print("Extraction failed. Skipping to next URL.")
            continue
            
        print("EXTRACTED JSON:")
        print(extracted_info.model_dump_json(indent=2))
        
        # 3. Geocoding
        print("\n--- PHASE 3: GEOCODING ---")
        if not extracted_info.places:
            print("No places extracted.")
        else:
            for idx, place in enumerate(extracted_info.places, 1):
                if not place.place_name:
                    print(f"\nPlace {idx}: Skipped (null/empty place_name)")
                    continue
                print(f"\nPlace {idx}: {place.place_name} ({place.city})")
                if place.place_name and place.city:
                    coords = geocode_place(place.place_name, place.city)
                    if coords:
                        lat, lon = coords
                        print(f"  Geocoded successfully: {lat}, {lon}")
                        print(f"  Google Maps Link: https://www.google.com/maps?q={lat},{lon}")
                    else:
                        print("  Geocoding returned no results.")
                else:
                    print("  Missing place_name or city, skipping geocoding.")

if __name__ == "__main__":
    run_test()
