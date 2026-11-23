"""Learn what a user likes from what they do, and how much to trust each creator.

Signals are the user's own actions (like, visit, open, dismiss). Each action is weighted, older actions
count less (exponential decay, half-life 30 days), and a per-key score is a smoothed signed average:

    score = (P - N) / (P + N + K)        in (-1, 1); K = 2 pseudo-observations of "no opinion"

so one like gives +0.33, three likes +0.6, and a dismissal pulls it back down. Smoothing stops a single
tap from swinging the ranking. `explore_bonus` is a UCB-style bonus 0.05 / sqrt(1 + n): categories the user
has barely seen get a small lift so the app keeps finding out what else they might like.
Pure functions over (action, key, age) tuples, so they are tested without a database.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

HALF_LIFE_DAYS = 30.0
PRIOR = 2.0
EXPLORE = 0.05
POSITIVE = {"like": 1.0, "visit": 1.5, "open": 0.3}
NEGATIVE = {"dismiss": 1.0}
SAVED_WEIGHT = 0.5  # the old "you saved things in this category" signal still counts, at half strength

Obs = Tuple[str, Optional[str], datetime]  # action, key (category or creator), when


def decay(created: datetime, now: datetime) -> float:
    age_days = max((now - created).total_seconds() / 86400.0, 0.0)
    return 0.5 ** (age_days / HALF_LIFE_DAYS)


def scores(observations: Iterable[Obs], now: datetime) -> Dict[str, Tuple[float, float]]:
    """key -> (signed score in (-1, 1), effective number of observations)."""
    pos: Dict[str, float] = {}
    neg: Dict[str, float] = {}
    for action, key, when in observations:
        if not key:
            continue
        w = decay(when, now)
        if action in POSITIVE:
            pos[key] = pos.get(key, 0.0) + POSITIVE[action] * w
        elif action in NEGATIVE:
            neg[key] = neg.get(key, 0.0) + NEGATIVE[action] * w
    out = {}
    for key in set(pos) | set(neg):
        p, n = pos.get(key, 0.0), neg.get(key, 0.0)
        out[key] = ((p - n) / (p + n + PRIOR), p + n)
    return out


def explore_bonus(n: float) -> float:
    return EXPLORE / math.sqrt(1.0 + max(n, 0.0))


def effective_interests(saved: Optional[Dict[str, float]], observations: Iterable[Obs], now: datetime,
                        categories: Iterable[str] = ()) -> Dict[str, float]:
    """Category -> interest in [0, 1] used by ranking: saved-habit signal + learned score + exploration bonus."""
    learned = scores(observations, now)
    keys = set(saved or {}) | set(learned) | set(categories)
    out = {}
    for c in keys:
        score, n = learned.get(c, (0.0, 0.0))
        value = SAVED_WEIGHT * (saved or {}).get(c, 0.0) + score + explore_bonus(n)
        out[c] = round(min(max(value, 0.0), 1.0), 3)
    return out


def explain(saved: Optional[Dict[str, float]], observations: Iterable[Obs], now: datetime) -> List[dict]:
    """Human-readable view of what was learned, strongest first (shown to the user: no hidden profiling)."""
    obs = list(observations)
    learned = scores(obs, now)
    effective = effective_interests(saved, obs, now)
    rows = []
    for c, value in effective.items():
        score, n = learned.get(c, (0.0, 0.0))
        rows.append({"category": c, "interest": value, "learned": round(score, 3), "evidence": round(n, 2),
                     "saved": round((saved or {}).get(c, 0.0), 3)})
    return sorted(rows, key=lambda r: -r["interest"])


def trust(observations: Iterable[Obs], now: datetime, grounded: int = 0, ungrounded: int = 0) -> float:
    """Creator reliability in (0, 1): Beta(1, 1) prior updated with how often their reels check out and how users react.

    Grounded items (quote found in the video) are successes, dropped ungrounded items are failures, likes and visits
    are successes and dismissals are failures. 0.5 means nothing is known.
    """
    s = float(grounded)
    f = float(ungrounded)
    for action, _key, when in observations:
        w = decay(when, now)
        if action in POSITIVE:
            s += POSITIVE[action] * w
        elif action in NEGATIVE:
            f += NEGATIVE[action] * w
    return round((s + 1.0) / (s + f + 2.0), 3)
