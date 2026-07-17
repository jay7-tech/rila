import os
import json
from pydantic import BaseModel, ValidationError
from typing import Optional, Literal, List
from datetime import date
from groq import Groq
import logging

logger = logging.getLogger(__name__)

class SinglePlace(BaseModel):
    place_name: Optional[str]
    city: Optional[str]
    category: Literal["food", "attraction", "store", "event", "other"]
    deal_description: Optional[str]
    deal_expiry: Optional[date]
    price_hint: Optional[str]
    confidence: float

class ExtractedPlaces(BaseModel):
    places: List[SinglePlace]

def extract_place_info(caption: str, transcript: str) -> Optional[ExtractedPlaces]:
    """
    Calls Groq API to extract structured place info from caption and transcript.
    Retries once on Pydantic validation failure.
    Returns None if it persistently fails.
    """
    groq_api_key = os.environ.get("GROQ_API_KEY")
    if not groq_api_key:
        print("Error: GROQ_API_KEY not found in environment.")
        return None
        
    client = Groq(api_key=groq_api_key)
    
    system_prompt = (
        "You are a specialized extraction assistant. Your task is to extract location and deal details from an Instagram reel's caption and transcript.\n"
        "Rules:\n"
        "1. Extract ONLY what is explicitly mentioned. Never invent or hallucinate a place_name, city, or price.\n"
        "2. If multiple distinct places are mentioned, extract each one as a separate entry in the places list. Do not combine multiple places into one place_name field.\n"
        "3. Only extract a place if it is a specific named business, venue, restaurant, or landmark that the reel is actually recommending or highlighting. Do not extract passing mentions, general dish names, media titles, or general locations. If in doubt, do not include it in the places list at all.\n"
        "4. If a field is not present, output null for it.\n"
        "5. If the caption or transcript establishes a shared city/locality ONCE (e.g. in a title, intro line, or hashtag) and then lists multiple places without repeating the city for each one, every place in that list should inherit that shared city unless a specific place explicitly states a different one. Example: if the video is titled 'Top 5 cafes in Koramangala' and then lists 'Third Wave Coffee, Blue Tokai, Dyu Art Cafe' without repeating the city each time, all three should have city: 'Koramangala', not null or 'Unknown'.\n"
        "6. The 'category' must be exactly one of: 'food', 'attraction', 'store', 'event', 'other'.\n"
        "7. The 'confidence' score must be a float between 0.0 and 1.0 representing your genuine estimate of how accurate this extraction is based on the provided text, and it must come from your own analysis, do not assume it's always 1.0.\n"
        "8. Output valid JSON matching this schema:\n"
        "{\n"
        "  \"places\": [\n"
        "    {\n"
        "      \"place_name\": string or null,\n"
        "      \"city\": string or null,\n"
        "      \"category\": string (from enum),\n"
        "      \"deal_description\": string or null,\n"
        "      \"deal_expiry\": string (YYYY-MM-DD) or null,\n"
        "      \"price_hint\": string or null,\n"
        "      \"confidence\": float\n"
        "    }\n"
        "  ]\n"
        "}"
    )
    
    user_prompt = f"Caption: {caption}\n\nTranscript: {transcript}"
    
    def call_llm(error_msg: str = None) -> Optional[str]:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        if error_msg:
            messages.append({"role": "user", "content": f"Your previous response failed validation with this error: {error_msg}. Please fix it and return valid JSON."})
            
        try:
            chat_completion = client.chat.completions.create(
                messages=messages,
                model="llama-3.3-70b-versatile",
                response_format={"type": "json_object"},
                temperature=0.0
            )
            return chat_completion.choices[0].message.content
        except Exception as e:
            print(f"Groq API call failed: {e}")
            return None

    # First attempt
    response_text = call_llm()
    if not response_text:
        return None
        
    extracted = None
    try:
        data = json.loads(response_text)
        extracted = ExtractedPlaces(**data)
    except (json.JSONDecodeError, ValidationError) as e:
        print(f"Extraction attempt 1 failed validation: {e}")
        # Retry once
        response_text_retry = call_llm(str(e))
        if response_text_retry:
            try:
                data_retry = json.loads(response_text_retry)
                extracted = ExtractedPlaces(**data_retry)
            except (json.JSONDecodeError, ValidationError) as e2:
                print(f"Extraction attempt 2 also failed: {e2}")
    
    if extracted:
        # Post-extraction filter
        kept_places = []
        stoplist = {"copper", "cry"}
        for place in extracted.places:
            p_name = (place.place_name or "").strip()
            p_city = (place.city or "").strip()
            
            drop_reason = None
            if place.confidence < 0.75:
                drop_reason = f"confidence {place.confidence} < 0.75"
            elif not p_city or p_city.lower() == "unknown":
                drop_reason = f"invalid city '{p_city}'"
            elif p_name:
                words = p_name.split()
                if len(words) == 1:
                    w = p_name.lower()
                    if w in stoplist:
                        drop_reason = f"single-word stoplist match '{p_name}'"
                    elif p_name.islower():
                        drop_reason = f"single lowercase word '{p_name}'"
            
            if drop_reason:
                logger.debug(f"[Filter] Dropped '{p_name}' in '{p_city}' (conf: {place.confidence:.2f}). Reason: {drop_reason}")
            else:
                logger.info(f"[Filter] Kept '{p_name}' in '{p_city}' (conf: {place.confidence:.2f})")
                kept_places.append(place)
                
        extracted.places = kept_places
        
    return extracted
