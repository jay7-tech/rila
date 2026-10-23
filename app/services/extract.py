"""Turn a reel's caption + transcript into structured items and live signals."""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, time
from typing import Callable, List, Literal, Optional

from pydantic import BaseModel, Field, ValidationError

from ..config import get_settings
from ..signals.models import ExtractedSignal

log = logging.getLogger(__name__)

Category = Literal["food", "store", "event", "education", "scheme", "service", "attraction", "other"]


class ExtractedItem(BaseModel):
    kind: Literal["place", "offer", "info"]
    category: Category = "other"
    title: Optional[str] = None  # short headline, e.g. "Buy one get one on cold brews"
    venue: Optional[str] = None  # business or place name used for map lookup
    city: Optional[str] = None
    area: Optional[str] = None  # neighbourhood or locality
    summary: Optional[str] = None
    price_text: Optional[str] = None
    duration_text: Optional[str] = None  # "weekdays 3-6pm", "this weekend"
    starts_at: Optional[str] = None  # ISO date or datetime
    ends_at: Optional[str] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ExtractionResult(BaseModel):
    items: List[ExtractedItem] = Field(default_factory=list)
    signals: List[ExtractedSignal] = Field(default_factory=list)


def build_prompt(now: datetime, posted_at: Optional[datetime]) -> str:
    posted = posted_at.strftime("%Y-%m-%d") if posted_at else "unknown"
    return (
        "You read the caption and transcript of a short video (Instagram Reel or YouTube Short) and "
        "extract useful local information. Output JSON only.\n"
        f"Today is {now.strftime('%A %Y-%m-%d')}. The video was posted on {posted}. Use these to "
        "turn words like 'this weekend', 'until Sunday' or 'tomorrow' into exact dates.\n\n"
        "Return {\"items\": [...], \"signals\": [...]}.\n\n"
        "ITEMS are things worth saving. Each item:\n"
        "  kind: 'place' (a specific venue to visit), 'offer' (a deal, discount, opening, event or class "
        "at a venue or area, with a time window), or 'info' (no physical place, e.g. a government scheme "
        "or scholarship).\n"
        "  category: food | store | event | education | scheme | service | attraction | other\n"
        "  title: short headline of what is on offer or why it is worth saving\n"
        "  venue: the business or place name exactly as spoken or written (null for info)\n"
        "  city, area: where it is. If the video names one city or locality once and then lists several "
        "venues, give every venue that city/area.\n"
        "  summary: one or two plain sentences; price_text: price or discount if stated; "
        "duration_text: the time window in the creator's words\n"
        "  starts_at, ends_at: ISO dates (YYYY-MM-DD) or datetimes (YYYY-MM-DDTHH:MM) if stated or "
        "clearly implied, else null\n"
        "  confidence: 0.0 to 1.0, your honest estimate that the extraction is accurate.\n\n"
        "SIGNALS are same-day observations about conditions, not offers. Each signal:\n"
        "  signal_type: crowd | weather | hazard | traffic | opening | closure | event | deal | price\n"
        "  area_name, city, value (short, e.g. 'packed', 'heavy rain', 'open'), severity 0-3 or null, "
        "summary, time_hint (words like 'today', 'yesterday'), confidence.\n\n"
        "Rules: extract only what is explicitly said. Never invent names, prices, dates or places. "
        "Skip passing mentions, generic chatter and media titles. If unsure, leave it out. Use null "
        "for unknown fields. If nothing useful is present return {\"items\": [], \"signals\": []}."
    )


def parse_dt(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO date/datetime. A bare end date means the end of that day."""
    if not value:
        return None
    v = value.strip()
    try:
        if len(v) <= 10:
            d = date.fromisoformat(v)
            return datetime.combine(d, time(23, 59))
        return datetime.fromisoformat(v.replace("Z", "")).replace(tzinfo=None)
    except ValueError:
        return None


def parse_start(value: Optional[str]) -> Optional[datetime]:
    """Like parse_dt but a bare date means the start of the day."""
    dt = parse_dt(value)
    if dt and value and len(value.strip()) <= 10:
        return datetime.combine(dt.date(), time(0, 0))
    return dt


def _models() -> List[str]:
    s = get_settings()
    chain = [s.groq_model] + [m for m in s.groq_fallback_models if m != s.groq_model]
    return [m for m in chain if m]


def _call_model(client, model: str, system: str, user: str, json_mode: bool) -> str:
    kwargs: dict = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.0,
    }
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if model.startswith("openai/gpt-oss"):
        kwargs["reasoning_effort"] = "low"  # extraction does not need long reasoning
    return client.chat.completions.create(**kwargs).choices[0].message.content or ""


def _groq_json(system: str, user: str) -> str:
    """Ask Groq for JSON. Moves to the next model if one is retired, unavailable or rate limited."""
    import groq

    client = groq.Groq(api_key=get_settings().groq_api_key)
    last: Optional[Exception] = None
    for model in _models():
        for json_mode in (True, False):
            try:
                return _call_model(client, model, system, user, json_mode)
            except groq.BadRequestError as e:  # this model may not support JSON mode: retry as plain text
                last = e
                log.warning("groq %s rejected request (json_mode=%s): %s", model, json_mode, str(e)[:200])
                continue
            except (groq.NotFoundError, groq.RateLimitError, groq.InternalServerError) as e:
                last = e
                log.warning("groq model %s unavailable, trying next: %s", model, type(e).__name__)
                break
    raise last or RuntimeError("no Groq model configured")


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.I)


def _loads_lenient(raw: str):
    """json.loads that also accepts code fences or text around a single JSON object."""
    text = _FENCE.sub("", (raw or "").strip())
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


def _clean(result: ExtractionResult, min_conf: float) -> ExtractionResult:
    items = []
    for it in result.items:
        title = (it.title or it.venue or "").strip()
        if not title or it.confidence < min_conf:
            continue
        if it.kind in ("place", "offer") and not (it.city or it.area):
            continue  # cannot place it on a map
        if it.kind == "place" and not (it.venue or it.title):
            continue
        if len(title.split()) == 1 and title.islower():
            continue  # single lowercase word is almost always noise
        it.title = title
        if it.kind == "place" and not it.venue:
            it.venue = title
        items.append(it)
    signals = [s for s in result.signals if s.confidence >= min_conf and (s.area_name or s.city)]
    return ExtractionResult(items=items, signals=signals)


def extract(
    caption: str,
    transcript: str,
    now: datetime,
    posted_at: Optional[datetime] = None,
    llm: Optional[Callable[[str, str], str]] = None,
    min_confidence: Optional[float] = None,
) -> ExtractionResult:
    """Run the LLM, validate, retry once on bad JSON, then filter."""
    call = llm or _groq_json
    min_conf = get_settings().min_confidence if min_confidence is None else min_confidence
    system = build_prompt(now, posted_at)
    user = f"Caption:\n{caption or ''}\n\nTranscript:\n{transcript or ''}"

    result: Optional[ExtractionResult] = None
    error = ""
    for attempt in range(2):
        prompt_user = user if not error else f"{user}\n\nYour previous reply was invalid: {error}\nReturn valid JSON only."
        raw = call(system, prompt_user)
        try:
            result = ExtractionResult(**_loads_lenient(raw))
            break
        except (json.JSONDecodeError, ValidationError, TypeError) as e:
            error = str(e)[:300]
            log.warning("extraction attempt %d invalid: %s", attempt + 1, error)
    if result is None:
        return ExtractionResult()
    return _clean(result, min_conf)
