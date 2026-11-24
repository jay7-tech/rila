"""Turn a video's caption, speech and on-screen text into structured items and live signals."""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, time
from typing import Callable, List, Literal, Optional

from pydantic import BaseModel, Field, ValidationError

from ..config import get_settings
from ..core.chunking import chunk_lines
from ..core.document import parse_rendered
from ..core.facts import check_dates, verify_price
from ..core.grounding import ground, looks_like_deal, looks_like_handle, name_in_source, name_line, strip_location_suffix, trim_name_fragment
from ..core.text import normalize
from ..signals.models import ExtractedSignal

log = logging.getLogger(__name__)

PROMPT_VERSION = "4"  # bump when the prompt changes; evaluation runs record it
MAX_CHARS_PER_CALL = 9000  # longer transcripts are read in overlapping chunks
CHUNK_TOKENS = 1500
NEAR_SECONDS = 8.0  # a quote this close to the line naming the venue is taken to be about that venue

Category = Literal["food", "store", "event", "education", "scheme", "service", "attraction", "other"]


class ExtractedItem(BaseModel):
    kind: Literal["place", "offer", "info"]
    category: Category = "other"
    title: Optional[str] = None  # short headline, e.g. "Buy one get one on cold brews"
    venue: Optional[str] = None  # business or place name exactly as spoken or written
    venue_en: Optional[str] = None  # the same name spelled in English letters, used for map lookup
    city: Optional[str] = None
    area: Optional[str] = None  # neighbourhood or locality
    summary: Optional[str] = None
    price_text: Optional[str] = None
    duration_text: Optional[str] = None  # "weekdays 3-6pm", "this weekend"
    starts_at: Optional[str] = None  # ISO date or datetime
    ends_at: Optional[str] = None
    evidence: Optional[str] = None  # verbatim quote that supports this item
    evidence_source: Optional[str] = None  # filled by grounding: speech | screen | text | caption
    evidence_at: Optional[float] = None  # seconds into the video, filled by grounding
    grounded: Optional[bool] = None  # None = no quote given, True = quote found, False = not found
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ExtractionResult(BaseModel):
    items: List[ExtractedItem] = Field(default_factory=list)
    signals: List[ExtractedSignal] = Field(default_factory=list)
    stats: dict = Field(default_factory=dict)


def build_prompt(now: datetime, posted_at: Optional[datetime]) -> str:
    posted = posted_at.strftime("%Y-%m-%d") if posted_at else "unknown"
    return (
        "You read a short video's caption, speech transcript and on-screen text and extract useful local "
        "information. Output JSON only.\n"
        "The video may be in any language or a mix of languages (for example Kannada, Hindi, Tamil, Telugu, "
        "English). Understand it in its original language. Lines look like '[0:12 speech] ...' or "
        "'[0:05-0:09 screen] ...': the tag says when and where the text came from. 'screen' lines are read by "
        "OCR and may contain mistakes; when they disagree with clear speech, trust the speech. Ignore "
        "watermarks, usernames, app buttons and subtitles of unrelated chatter.\n"
        f"Today is {now.strftime('%A %Y-%m-%d')}. The video was posted on {posted}. Use these to "
        "turn words like 'this weekend', 'until Sunday' or 'tomorrow' into exact dates.\n\n"
        "Return {\"items\": [...], \"signals\": [...]}.\n\n"
        "ITEMS are things worth saving. Each item:\n"
        "  kind: 'place' (a specific venue to visit), 'offer' (a deal, discount, opening, event or class "
        "at a venue or area, with a time window), or 'info' (no physical place, e.g. a government scheme "
        "or scholarship).\n"
        "  category: food | store | event | education | scheme | service | attraction | other\n"
        "  title: short headline in English of what is on offer or why it is worth saving\n"
        "  venue: the business or place name exactly as spoken or written, in its original script "
        "(null for info)\n"
        "  venue_en: the same name spelled with English letters (transliterate if needed)\n"
        "  city, area: where it is, in English. If the video names one city or locality once and then lists "
        "several venues, give every venue that city/area. If speech never says the city but the caption or "
        "hashtags do (for example #bengaluru or 'Bangalore'), use that city.\n"
        "  summary: one or two plain English sentences; price_text: price or discount if stated; "
        "duration_text: the time window in the creator's words\n"
        "  starts_at, ends_at: ISO dates (YYYY-MM-DD) or datetimes (YYYY-MM-DDTHH:MM), ONLY when the "
        "video states a validity period for a deal, event, class or scheme. Never use the posting date or "
        "today's date as a start or end date. Recommendation lists of restaurants or places have no "
        "dates: use null. For kind 'place' always use null.\n"
        "  evidence: ONE exact quote copied word for word from the caption or a line (same language and "
        "script, at most 200 characters, without the [time source] tag) that proves this item. Never "
        "translate or paraphrase it.\n"
        "  confidence: 0.0 to 1.0, your honest estimate that the extraction is accurate.\n\n"
        "SIGNALS are same-day observations about conditions, not offers. Each signal:\n"
        "  signal_type: crowd | weather | hazard | traffic | opening | closure | event | deal | price\n"
        "  area_name, city (English), value (short, e.g. 'packed', 'heavy rain', 'open'), severity 0-3 or "
        "null, summary, time_hint (words like 'today', 'yesterday'), confidence.\n\n"
        "Rules: extract only what is explicitly said or shown. Never invent names, prices, dates or places. "
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


_DEAL_CLAIM = re.compile(
    r"\b(off|free|offer|offers|discount|deal|deals|combo|bogo|buy|launch|launching|opening|inaugur\w*|sale|cashback|"
    r"coupon|limited|till|until|first \d+)\b|%|\bjust\s*(?:rs\.?|₹)?\s*\d",
    re.I,
)


def _tidy_names(result: ExtractionResult) -> ExtractionResult:
    """Repair venue names before they are checked: cut OCR fragments, and never keep a deal headline as a venue."""
    for it in result.items:
        for field in ("venue", "venue_en"):
            value = getattr(it, field)
            if looks_like_deal(value) or looks_like_handle(value):
                setattr(it, field, None)
            else:
                value = trim_name_fragment(value)
                setattr(it, field, strip_location_suffix(value, it.area, it.city))
        if it.area and it.city and normalize(it.area) == normalize(it.city):
            it.area = None  # "Bangalore, Bangalore"
    return result


def _is_real_offer(it: ExtractedItem) -> bool:
    """An offer needs a validity window or a deal claim; a plain price list is just a place."""
    if it.starts_at or it.ends_at or it.duration_text:
        return True
    text = " ".join(filter(None, [it.title, it.summary, it.evidence]))
    return bool(_DEAL_CLAIM.search(text))


def _clean(result: ExtractionResult, min_conf: float) -> ExtractionResult:
    items = []
    for it in result.items:
        title = (it.title or it.venue_en or it.venue or "").strip()
        if not title or it.confidence < min_conf:
            continue
        named = bool(it.venue or it.venue_en)
        if it.kind == "place" and not named:
            continue  # "a smash burger spot" is not something you can go to
        if it.kind in ("place", "offer") and not (it.city or it.area) and not named:
            continue  # no name and no location: nothing to find. A named place without a city is kept, unpinned.
        if len(title.split()) == 1 and title.islower():
            continue  # single lowercase word is almost always noise
        it.title = title
        if it.kind == "offer" and named and not _is_real_offer(it):
            it.kind = "place"  # menu prices and reviews are not deals
        if it.kind == "place" and not it.venue:
            it.venue = it.venue_en or title
        items.append(it)
    signals = [s for s in result.signals if s.confidence >= min_conf and (s.area_name or s.city)]
    return ExtractionResult(items=items, signals=signals, stats=result.stats)


def _ground(result: ExtractionResult, caption: str, transcript: str) -> ExtractionResult:
    """Keep only items whose evidence quote really occurs in the video. Items without a quote stay, unverified."""
    lines = parse_rendered(transcript)
    kept, dropped, unnamed = [], 0, 0
    for it in result.items:
        if it.kind in ("place", "offer") and (it.venue or it.venue_en) and not name_in_source([it.venue, it.venue_en], lines, caption):
            unnamed += 1  # nobody said or showed this name
            log.info("dropped item whose name is not in the video: %r", (it.venue_en or it.venue or "")[:60])
            continue
        if it.evidence and it.evidence.strip():
            hit = ground(it.evidence, lines, caption)
            if hit is None:
                it.grounded = False
                dropped += 1
                log.info("dropped ungrounded item %r", (it.title or it.venue or "")[:60])
                continue
            it.grounded, it.evidence_source, it.evidence_at = True, hit.source, hit.start
            names = [it.venue, it.venue_en]
            if it.kind in ("place", "offer") and any(names) and not name_in_source(names, [], it.evidence):
                # the quote is real but does not mention the venue (it is about a price, say): quote the line that does
                anchor = name_line(names, lines, caption)
                near = anchor is not None and anchor.start is not None and hit.start is not None and abs(anchor.start - hit.start) <= NEAR_SECONDS
                if anchor is not None and anchor.text and not near:  # a quote seconds from the name still supports the item
                    it.evidence, it.evidence_source, it.evidence_at = anchor.text, anchor.source, anchor.start
        kept.append(it)
    result.items = kept
    result.stats["ungrounded_dropped"] = result.stats.get("ungrounded_dropped", 0) + dropped + unnamed  # invented quote or invented name
    result.stats["name_not_found_dropped"] = result.stats.get("name_not_found_dropped", 0) + unnamed
    return result


def _verify_facts(result: ExtractionResult, caption: str, transcript: str, now: datetime, posted_at: Optional[datetime]) -> ExtractionResult:
    """Check prices against the source and repair dates with code (the model is bad at date arithmetic)."""
    source = " ".join([caption or "", transcript or ""])
    st = result.stats
    for it in result.items:
        ok = verify_price(it.price_text, source)
        if ok is False:
            it.price_text = None  # a number nobody said or showed
            it.confidence = max(0.0, it.confidence - 0.1)
            st["price_dropped"] = st.get("price_dropped", 0) + 1
        starts, ends, notes = check_dates(it.kind, parse_start(it.starts_at), parse_dt(it.ends_at),
                                          [it.duration_text, it.evidence, it.summary], now, posted_at)
        if notes:
            it.starts_at = starts.isoformat(timespec="minutes") if starts else None
            it.ends_at = ends.isoformat(timespec="minutes") if ends else None
            for n in notes:
                st[n] = st.get(n, 0) + 1
    return result


def _extract_once(call: Callable[[str, str], str], system: str, user: str) -> Optional[ExtractionResult]:
    """One LLM call, validated, with one retry on bad JSON."""
    error = ""
    for attempt in range(2):
        prompt_user = user if not error else f"{user}\n\nYour previous reply was invalid: {error}\nReturn valid JSON only."
        raw = call(system, prompt_user)
        try:
            return ExtractionResult(**_loads_lenient(raw))
        except (json.JSONDecodeError, ValidationError, TypeError) as e:
            error = str(e)[:300]
            log.warning("extraction attempt %d invalid: %s", attempt + 1, error)
    return None


def _user_message(caption: str, body: str) -> str:
    return f"Caption:\n{caption or ''}\n\nSpeech and on-screen text:\n{body or ''}"


def _windows(transcript: str) -> List[str]:
    """The transcript as one piece, or as overlapping chunks when it is too long for one call."""
    if len(transcript) <= MAX_CHARS_PER_CALL:
        return [transcript]
    raw_lines = [l for l in transcript.splitlines() if l.strip()]
    chunks = chunk_lines(parse_rendered("\n".join(raw_lines)), max_tokens=CHUNK_TOKENS, overlap_tokens=150)
    out: List[str] = []
    for c in chunks:
        piece = "\n".join(raw_lines[c.first_line:c.last_line + 1])
        # a single enormous line (a transcript with no line breaks) is sliced by length
        out += [piece[i:i + MAX_CHARS_PER_CALL] for i in range(0, len(piece), MAX_CHARS_PER_CALL)]
    return out


def _dedupe(items: List[ExtractedItem]) -> List[ExtractedItem]:
    """One item per venue within a video. If the same venue came out as a place and as an offer, keep the more
    informative one: a real offer beats a place; otherwise the first seen wins."""
    best: dict = {}
    order: List[str] = []
    for it in items:
        name = normalize(it.venue_en or it.venue or "")
        key = name or "\0" + normalize(it.title)  # unnamed items are only merged when their titles match
        if key not in best:
            best[key] = it
            order.append(key)
        elif it.kind == "offer" and best[key].kind != "offer" and _is_real_offer(it):
            best[key] = it
        elif it.kind == best[key].kind and name == "":
            continue
    return [best[k] for k in order]


def extract(
    caption: str,
    transcript: str,
    now: datetime,
    posted_at: Optional[datetime] = None,
    llm: Optional[Callable[[str, str], str]] = None,
    min_confidence: Optional[float] = None,
) -> ExtractionResult:
    """Run the LLM, validate, check the evidence, then filter. Long videos are read in chunks."""
    call = llm or _groq_json
    min_conf = get_settings().min_confidence if min_confidence is None else min_confidence
    system = build_prompt(now, posted_at)

    merged = ExtractionResult()
    for window in _windows(transcript or ""):
        part = _extract_once(call, system, _user_message(caption, window))
        if part is None:
            continue
        merged.items += part.items
        merged.signals += part.signals
    merged.items = _dedupe(merged.items)
    merged = _tidy_names(merged)
    merged = _ground(merged, caption or "", transcript or "")
    merged = _verify_facts(merged, caption or "", transcript or "", now, posted_at)
    return _clean(merged, min_conf)
