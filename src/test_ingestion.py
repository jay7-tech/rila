import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.pipeline.ingestion import process_reel
from src.pipeline.extraction import extract_place_info
import logging

logging.basicConfig(level=logging.DEBUG)

def test_url(url):
    print(f"\n======================================")
    print(f"Testing URL: {url}")
    print(f"======================================")
    caption, transcript = process_reel(url)
    if caption is None and transcript is None:
        print("Download failed.")
        return
        
    print(f"\nCaption length: {len(caption)}")
    print(f"Transcript length: {len(transcript)}")
    print(f"Transcript preview: {transcript[:200]}...")
    
    print("\nRunning extraction...")
    extracted_info = extract_place_info(caption, transcript)
    if not extracted_info or not extracted_info.places:
        print("No valid places extracted.")
    else:
        for p in extracted_info.places:
            print(f" - Kept: {p.place_name} ({p.city}) [conf: {p.confidence}]")

if __name__ == "__main__":
    urls = [
        "https://youtube.com/shorts/3kMaG0BADDA",
        "https://youtube.com/shorts/cQZ9q6ERKMA",
        "https://youtube.com/shorts/YGpdkQpky7M"
    ]
    for u in urls:
        test_url(u)
