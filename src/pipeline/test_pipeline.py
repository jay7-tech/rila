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
        if not extracted_info.entities:
            print("No entities extracted.")
        else:
            for idx, entity in enumerate(extracted_info.entities, 1):
                if not entity.title:
                    print(f"\nEntity {idx}: Skipped (null/empty title)")
                    continue
                print(f"\nEntity {idx}: {entity.title} ({entity.city})")
                if entity.title and entity.city:
                    lat, lon, status = geocode_place(entity.title, entity.city)
                    if status == "confirmed":
                        print(f"  Geocoded successfully: {lat}, {lon}")
                        print(f"  Google Maps Link: https://www.google.com/maps?q={lat},{lon}")
                    else:
                        print(f"  Geocoding status: {status}")
                else:
                    print("  Missing title or city, skipping geocoding.")

if __name__ == "__main__":
    run_test()
