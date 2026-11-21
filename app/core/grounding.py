"""Check that a quoted piece of evidence really appears in what the video said or showed."""
from __future__ import annotations

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
