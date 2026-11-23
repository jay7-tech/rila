"""Check that a quoted piece of evidence really appears in what the video said or showed."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence

from rapidfuzz import fuzz

from .document import Line
from .text import normalize

MIN_QUOTE_CHARS = 4
THRESHOLD = 85  # rapidfuzz partial_ratio 0-100: tolerates OCR slips and small ASR differences


@dataclass
class Hit:
    source: str  # speech | screen | text | caption | multi
    start: Optional[float]
    score: float
    text: str = ""  # the quotable line, when known


def _score(q: str, hay: str) -> float:
    return 100.0 if q in hay else fuzz.partial_ratio(q, hay)


def _first_line(q: str, lines: Sequence[Line], source: Optional[str]) -> Optional[float]:
    """Time of the first line (of this source) that contains the opening words of the quote."""
    prefix = " ".join(q.split()[:3])
    for l in lines:
        if source and l.source != source:
            continue
        hay = normalize(l.text)
        if hay and len(hay) >= len(prefix) and _score(prefix, hay) >= 90:
            return l.start
    return None


def ground(quote: Optional[str], lines: Sequence[Line], caption: str = "", threshold: int = THRESHOLD) -> Optional[Hit]:
    """Best place the quote occurs, or None when it is not in the source (a likely invention).

    A line much shorter than the quote can never prove it: fuzzy matching would otherwise accept a
    4-letter OCR fragment that happens to sit inside a long quote.
    """
    q = normalize(quote)
    if len(q) < MIN_QUOTE_CHARS:
        return None
    best: Optional[Hit] = None
    candidates = [("caption", None, normalize(caption))] + [(l.source, l.start, normalize(l.text)) for l in lines]
    for source, start, hay in candidates:
        if not hay or len(hay) < 0.6 * len(q):
            continue
        score = _score(q, hay)
        if score >= threshold and (best is None or score > best.score):
            best = Hit(source, start, score)
    if best is not None:
        return best
    # The quote may run across neighbouring lines: try each source on its own (so screen noise between
    # spoken lines does not break the match), then everything together.
    for source in ("speech", "screen", "text", None):
        pool = [l for l in lines if source is None or l.source == source]
        joined = " ".join(h for h in (normalize(l.text) for l in pool) if h)
        if joined and _score(q, joined) >= threshold:
            return Hit(source or "multi", _first_line(q, lines, source), _score(q, joined))
    return None


def name_in_source(names: Sequence[Optional[str]], lines: Sequence[Line], caption: str = "") -> bool:
    """True when at least one spelling of a venue name occurs in the caption, speech or on-screen text.

    Guards against a model that attaches a plausible-sounding name which nobody said. Names shorter than
    3 letters cannot be checked and are given the benefit of the doubt; 3-4 letter names (CTR, MTR) must
    appear exactly.
    """
    haystack = " ".join(h for h in [normalize(caption)] + [normalize(l.text) for l in lines] if h)
    checked = False
    for name in names:
        n = normalize(name)
        if len(n) < 3:
            continue
        checked = True
        if n in haystack or (len(n) >= 5 and fuzz.partial_ratio(n, haystack) >= THRESHOLD):
            return True
    return not checked


_GENERIC_WORDS = ("restaurant", "resturant", "restaurants", "hotel", "canteen", "kitchen", "bakery", "bhavan", "tiffins", "cafe")
_DEAL_WORDS = re.compile(r"\b(rs\.?|inr|off|free|just|only|offer|combo|discount)\b|[₹%]", re.I)


def trim_name_fragment(name: Optional[str]) -> Optional[str]:
    """Drop a leading OCR fragment of a generic word: 'RANT MAJLIS MANDI' (cut from 'RESTAURANT') -> 'MAJLIS MANDI'."""
    if not name:
        return name
    parts = name.split()
    if len(parts) >= 3:
        first = re.sub(r"[^A-Za-z]", "", parts[0]).lower()
        if 3 <= len(first) <= 6 and any(w != first and w.endswith(first) for w in _GENERIC_WORDS):
            return " ".join(parts[1:])
    return name


def looks_like_deal(name: Optional[str]) -> bool:
    """'Fish meals just 99 rs' is an offer headline, not a venue name."""
    n = name or ""
    return len(n.split()) >= 3 and any(c.isdigit() for c in n) and bool(_DEAL_WORDS.search(n))


def name_line(names: Sequence[Optional[str]], lines: Sequence[Line], caption: str = "", max_chars: int = 200) -> Optional[Hit]:
    """The line (or caption sentence) that actually mentions the venue, returned as a Hit whose `text` is quotable.

    Used to replace an evidence quote that is real but about something else (a price) with the line that names the venue.
    Prefers spoken or shown lines over the caption, and the earliest one.
    """
    wanted = [normalize(n) for n in names if n and len(normalize(n)) >= 3]
    if not wanted:
        return None

    def mentions(text: str) -> bool:
        hay = normalize(text)
        return bool(hay) and any(n in hay or (len(n) >= 5 and fuzz.partial_ratio(n, hay) >= THRESHOLD) for n in wanted)

    for l in lines:
        if l.source in ("speech", "screen", "text") and mentions(l.text) and len(l.text.strip()) <= max_chars:
            return Hit(l.source, l.start, 100.0, l.text.strip())
    for sentence in re.split(r"(?<=[.!?])\s+|\n+", caption or ""):
        if sentence.strip() and mentions(sentence):
            return Hit("caption", None, 100.0, sentence.strip()[:max_chars])
    return None


def looks_like_handle(name: Optional[str]) -> bool:
    """'Tasteofbengaluru_' is a social-media watermark, not a venue: one token with an underscore or a dot."""
    n = (name or "").strip()
    return bool(n) and " " not in n and (n.startswith("@") or "_" in n or n.count(".") >= 1 and n.replace(".", "").isalnum() and n.islower())


def strip_location_suffix(name: Optional[str], *places: Optional[str]) -> Optional[str]:
    """'Dolphins in Cox Town' -> 'Dolphins' when Cox Town is the item's area or city."""
    if not name:
        return name
    low = name.lower()
    for place in places:
        if place:
            for joiner in (" in ", " at ", ", "):
                tail = joiner + place.strip().lower()
                if low.endswith(tail) and len(name) > len(tail):
                    return name[: len(name) - len(tail)].strip()
    return name
