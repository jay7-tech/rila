"""Deterministic checks on the facts an LLM attaches to an item: prices and dates.

The model is good at reading; it is unreliable at arithmetic ("until Sunday" -> a date) and sometimes
invents a number. Everything here is plain code that can be tested and that never calls a model.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import Iterable, Optional, Set

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def numbers_in(text: Optional[str]) -> Set[str]:
    """Every number in the text as a plain ASCII string. Indic digits are converted: '९९' -> '99', '1,299' -> '1299'."""
    ascii_text = "".join(
        str(unicodedata.digit(c)) if unicodedata.category(c) == "Nd" and not c.isascii() else c for c in (text or "")
    )
    out: Set[str] = set()
    for m in _NUM.finditer(ascii_text):
        n = m.group(0).replace(",", "").rstrip(".")
        if "." in n:
            n = n.rstrip("0").rstrip(".")
        out.add(n.lstrip("0") or "0")
    return out


def verify_price(price_text: Optional[str], source: str) -> Optional[bool]:
    """True: every number in the price appears in the source. False: the source uses digits but not these.
    None: nothing to check (no number in the price, or the source says its numbers in words)."""
    wanted = numbers_in(price_text)
    if not wanted:
        return None
    have = numbers_in(source)
    if wanted <= have:
        return True
    return False if have else None


_MONTHS = {m: i for i, names in enumerate(
    [("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"), ("may",), ("jun", "june"),
     ("jul", "july"), ("aug", "august"), ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
     ("dec", "december")], start=1) for m in names}
_MON = "|".join(sorted(_MONTHS, key=len, reverse=True))
_DAY_MON = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s*(?:of\s+)?({_MON})\b", re.I)
_MON_DAY = re.compile(rf"\b({_MON})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", re.I)
_WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6,
             "mon": 0, "tue": 1, "tues": 1, "wed": 2, "thu": 3, "thur": 3, "thurs": 3, "fri": 4, "sat": 5, "sun": 6}
_END_CUE = re.compile(r"\b(till|until|upto|up to|through|thru|ends?|ending|valid (?:till|until)|last day|by)\b", re.I)


def _safe_date(y: int, m: int, d: int) -> Optional[date]:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def explicit_end_date(text: Optional[str], ref: date) -> Optional[date]:
    """A date the creator spelled out as an end ('till 30 Nov', 'until Sunday'), resolved against `ref`
    (the posting date). Only fires when an end cue sits right before it. None when nothing is spelled out."""
    t = text or ""
    for rx, day_first in ((_DAY_MON, True), (_MON_DAY, False)):
        for m in rx.finditer(t):
            if not _END_CUE.search(t[max(0, m.start() - 24):m.start()]):
                continue
            d, mon = (int(m.group(1)), _MONTHS[m.group(2).lower()]) if day_first else (int(m.group(2)), _MONTHS[m.group(1).lower()])
            found = _safe_date(ref.year, mon, d)
            if found and found < ref - timedelta(days=30):
                found = _safe_date(ref.year + 1, mon, d)
            if found:
                return found
    for m in re.finditer(r"\b(" + "|".join(sorted(_WEEKDAYS, key=len, reverse=True)) + r")\b", t, re.I):
        if _END_CUE.search(t[max(0, m.start() - 24):m.start()]):
            wd = _WEEKDAYS[m.group(1).lower()]
            return ref + timedelta(days=(wd - ref.weekday()) % 7)
    return None


MAX_AHEAD_DAYS = 400


def check_dates(kind: str, starts: Optional[datetime], ends: Optional[datetime], texts: Iterable[Optional[str]],
                now: datetime, posted_at: Optional[datetime]):
    """Return (starts, ends, notes). Corrects or drops dates that cannot be right; notes say what happened."""
    notes = []
    if kind == "place":
        return None, None, (["dates_dropped"] if starts or ends else [])
    ref = (posted_at or now).date()
    spelled = explicit_end_date(" ".join(t for t in texts if t), ref)
    if spelled is not None:
        if ends is None or ends.date() != spelled:
            ends = datetime.combine(spelled, datetime.max.time().replace(microsecond=0, second=0))
            notes.append("end_corrected")
        if starts and starts > ends:
            starts = None
    if starts and ends and ends < starts:
        starts = ends = None
        notes.append("dates_dropped")
    if ends and ends.date() < ref - timedelta(days=1):
        ends = None
        notes.append("end_before_posting")
    for name in ("starts", "ends"):
        v = starts if name == "starts" else ends
        if v and v.date() > now.date() + timedelta(days=MAX_AHEAD_DAYS):
            if name == "starts":
                starts = None
            else:
                ends = None
            notes.append("date_too_far")
    return starts, ends, notes
