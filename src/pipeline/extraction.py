import os
import json
from pydantic import BaseModel, ValidationError
from typing import Optional, Literal, List
from datetime import date
from groq import Groq

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
        "5. The 'category' must be exactly one of: 'food', 'attraction', 'store', 'event', 'other'.\n"
        "6. The 'confidence' score must be a float between 0.0 and 1.0 representing your genuine estimate of how accurate this extraction is based on the provided text, and it must come from your own analysis, do not assume it's always 1.0.\n"
        "7. Output valid JSON matching this schema:\n"
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
        
    try:
        data = json.loads(response_text)
        return ExtractedPlaces(**data)
    except (json.JSONDecodeError, ValidationError) as e:
        print(f"Extraction attempt 1 failed validation: {e}")
        # Retry once
        response_text_retry = call_llm(str(e))
        if not response_text_retry:
            return None
        try:
            data_retry = json.loads(response_text_retry)
            return ExtractedPlaces(**data_retry)
        except (json.JSONDecodeError, ValidationError) as e2:
            print(f"Extraction attempt 2 also failed: {e2}")
            return None
