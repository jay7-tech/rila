"""Freshness decay and time resolution for signals."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional

# Hours after which a signal's weight halves. Fast-changing things decay fast.
HALF_LIFE_HOURS = {
    "traffic": 3,
    "crowd": 6,
    "weather": 8,
    "hazard": 24,
    "event": 48,
    "deal": 72,
    "opening": 72,
    "closure": 72,
    "price": 168,
}

# Below this weight a signal is dropped from briefings.
MIN_FRESHNESS = 0.1


def freshness(signal_type: str, observed_at: datetime, now: datetime) -> float:
    """1.0 when just observed, 0.5 after one half-life, never negative."""
    half_life = HALF_LIFE_HOURS.get(signal_type, 24)
    age_hours = max((now - observed_at).total_seconds() / 3600.0, 0.0)
    return 0.5 ** (age_hours / half_life)


_HINT_OFFSETS = [
    (re.compile(r"\b(last week|week ago)\b", re.I), timedelta(days=7)),
    (re.compile(r"\b(day before yesterday)\b", re.I), timedelta(days=2)),
    (re.compile(r"\b(yesterday|last night)\b", re.I), timedelta(days=1)),
]


def resolve_observed_at(
    time_hint: Optional[str], posted_at: Optional[datetime], now: datetime
) -> datetime:
    """Best estimate of when something was observed.

    Uses the reel's post time when known (else now), then subtracts any
    explicit relative wording ("yesterday") the creator used.
    """
    base = posted_at or now
    if time_hint:
        for pattern, offset in _HINT_OFFSETS:
            if pattern.search(time_hint):
                return base - offset
    return base


def age_label(observed_at: datetime, now: datetime) -> str:
    """Human label such as '35 min ago', '5h ago', '2d ago'."""
    minutes = int(max((now - observed_at).total_seconds(), 0) // 60)
    if minutes < 60:
        return f"{max(minutes, 1)} min ago"
    hours = minutes // 60
    if hours < 48:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"
