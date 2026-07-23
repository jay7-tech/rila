import os
import json
from pydantic import BaseModel, ValidationError
from typing import Optional, Literal, List
from datetime import date
from groq import Groq
import groq
import logging

logger = logging.getLogger(__name__)

class ExtractedEntity(BaseModel):
    content_type: Literal["place", "info"]
    category: Literal["food", "attraction", "store", "hidden_gem", "event", "deal", "scheme", "education", "other"]
    title: Optional[str]
    city: Optional[str]
    summary: Optional[str]
    key_details: Optional[str]
    expiry_or_deadline: Optional[date]
    confidence: float

class ExtractedEntities(BaseModel):
    entities: List[ExtractedEntity]

def extract_place_info(caption: str, transcript: str) -> Optional[ExtractedEntities]:
    """
    Calls Groq API to extract structured entities (places, deals, hidden gems,
    schemes, educational info, etc.) from a reel's caption and transcript.
    Retries once on Pydantic validation failure.
    Returns None if it persistently fails.
    """
    groq_api_key = os.environ.get("GROQ_API_KEY")
    if not groq_api_key:
        print("Error: GROQ_API_KEY not found in environment.")
        return None

    client = Groq(api_key=groq_api_key)

    system_prompt = (
        "You are a specialized extraction assistant. Your task is to extract every distinct, "
        "concrete, useful entity mentioned in a reel's caption and transcript. This is broader "
        "than just restaurants — it includes physical places (restaurants, cafes, stores, "
        "attractions, hidden gems), retail deals/offers, and non-physical informational content "
        "like government schemes/programs, scholarships, or educational information.\n"
        "Rules:\n"
        "1. Extract ONLY what is explicitly mentioned. Never invent or hallucinate a title, city, "
        "amount, deadline, or any other field.\n"
        "2. If multiple distinct entities are mentioned, extract each one as a separate entry. Do "
        "not combine multiple entities into one title field.\n"
        "3. Only extract an entity if it is something specific and actionable the reel is actually "
        "recommending, highlighting, or explaining — not a passing mention, a general topic, a media "
        "title, or vague chatter. If in doubt, do not include it.\n"
        "4. If a field is not present, output null for it.\n"
        "5. Set content_type to 'place' ONLY if this entity is a specific physical location someone "
        "could travel to (a named restaurant, cafe, store, attraction, or hidden gem). Set "
        "content_type to 'info' for anything with no single physical address to visit — this "
        "includes deals/offers not tied to one store, government schemes, scholarships, educational "
        "programs, or general informational content.\n"
        "6. If the caption or transcript establishes a shared city/locality ONCE (e.g. in a title, "
        "intro line, or hashtag) and then lists multiple 'place' entities without repeating the city "
        "for each one, every place in that list should inherit that shared city unless a specific "
        "entity explicitly states a different one. Example: if the video is titled 'Top 5 cafes in "
        "Koramangala' and then lists 'Third Wave Coffee, Blue Tokai, Dyu Art Cafe' without repeating "
        "the city each time, all three should have city: 'Koramangala', not null or 'Unknown'. This "
        "rule does not apply to content_type 'info' entities — leave city null for those unless a "
        "city is genuinely part of the info (e.g. a scheme that is state-specific).\n"
        "7. The 'category' must be exactly one of: 'food', 'attraction', 'store', 'hidden_gem', "
        "'event', 'deal', 'scheme', 'education', 'other'.\n"
        "8. 'summary' should be one or two sentences capturing what makes this entity worth saving — "
        "the specific dish/vibe/specialty for a place, what the deal actually offers, or what the "
        "scheme/program provides. Do not pad this with generic filler.\n"
        "9. 'key_details' should hold the single most important concrete fact if one exists — a "
        "price, a discount amount, eligibility criteria, an application step, or similar. Null if "
        "nothing concrete was stated.\n"
        "10. 'expiry_or_deadline' is any explicitly stated date after which this is no longer valid "
        "(a deal's expiry, a scheme's application deadline). Null if none was stated.\n"
        "11. The 'confidence' score must be a float between 0.0 and 1.0 representing your genuine "
        "estimate of how accurate this extraction is based on the provided text, and it must come "
        "from your own analysis — do not default to 1.0.\n"
        "12. Output valid JSON matching this schema:\n"
        "{\n"
        "  \"entities\": [\n"
        "    {\n"
        "      \"content_type\": \"place\" or \"info\",\n"
        "      \"category\": string (from enum),\n"
        "      \"title\": string or null,\n"
        "      \"city\": string or null,\n"
        "      \"summary\": string or null,\n"
        "      \"key_details\": string or null,\n"
        "      \"expiry_or_deadline\": string (YYYY-MM-DD) or null,\n"
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
        except groq.RateLimitError as e:
            raise e
        except Exception as e:
            print(f"Groq API call failed: {e}")
            return None

    response_text = call_llm()
    if not response_text:
        return None

    extracted = None
    try:
        data = json.loads(response_text)
        extracted = ExtractedEntities(**data)
    except (json.JSONDecodeError, ValidationError) as e:
        print(f"Extraction attempt 1 failed validation: {e}")
        response_text_retry = call_llm(str(e))
        if response_text_retry:
            try:
                data_retry = json.loads(response_text_retry)
                extracted = ExtractedEntities(**data_retry)
            except (json.JSONDecodeError, ValidationError) as e2:
                print(f"Extraction attempt 2 also failed: {e2}")

    if extracted:
        kept_entities = []
        stoplist = {"copper", "cry"}
        for entity in extracted.entities:
            e_title = (entity.title or "").strip()
            e_city = (entity.city or "").strip()

            drop_reason = None
            if entity.confidence < 0.75:
                drop_reason = f"confidence {entity.confidence} < 0.75"
            elif not e_title:
                drop_reason = "empty title"
            elif entity.content_type == "place" and (not e_city or e_city.lower() == "unknown"):
                drop_reason = f"invalid city '{e_city}' for a place-type entity"
            elif e_title:
                words = e_title.split()
                if len(words) == 1:
                    w = e_title.lower()
                    if w in stoplist:
                        drop_reason = f"single-word stoplist match '{e_title}'"
                    elif e_title.islower():
                        drop_reason = f"single lowercase word '{e_title}'"

            if drop_reason:
                logger.debug(f"[Filter] Dropped '{e_title}' ({entity.content_type}) in '{e_city}' (conf: {entity.confidence:.2f}). Reason: {drop_reason}")
            else:
                logger.info(f"[Filter] Kept '{e_title}' ({entity.content_type}) in '{e_city}' (conf: {entity.confidence:.2f})")
                kept_entities.append(entity)

        extracted.entities = kept_entities

    return extracted
