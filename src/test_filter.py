import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.pipeline.extraction import extract_place_info
import logging
import json

logging.basicConfig(level=logging.INFO)

if __name__ == "__main__":
    caption = "Top 5 recommendations in Indiranagar! 1. Third Wave Coffee 2. Blue Tokai 3. Dyu Art Cafe 4. Toit 5. Truffles"
    transcript = "Here are the top 5 places to visit in Indiranagar. First is Third Wave Coffee, great coffee. Second is Blue Tokai. Then we have Dyu Art Cafe. Next is Toit for some drinks. And finally Truffles for a great burger."
    
    print("\nRunning extraction on Indiranagar Top 5...")
    extracted_info = extract_place_info(caption, transcript)
    if not extracted_info or not extracted_info.places:
        print("No valid places extracted.")
    else:
        print("\nExtracted JSON:")
        # We need to dump it properly
        out_list = []
        for p in extracted_info.places:
            out_list.append({
                "place_name": p.place_name,
                "city": p.city,
                "confidence": p.confidence
            })
        print(json.dumps({"places": out_list}, indent=2))
