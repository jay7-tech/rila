"""Rank a user's offers/places for 'near me' and 'ask Rilla' recommendations."""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, List, Optional

from .offers import Offer, status, time_left_label

EARTH_M = 6371000.0


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_M * math.atan2(math.sqrt(a), math.sqrt(1 - a))


@dataclass
class Recommendation:
    offer: Offer
    score: float
    distance_m: Optional[float]
    label: str
    reasons: List[str]


def _distance_score(d: Optional[float], radius_m: float) -> float:
    if d is None:
        return 0.15
    if d > radius_m:
        return 0.0
    return 1.0 - (d / radius_m) ** 0.7  # steep near, gentle far


def _urgency_score(offer: Offer, now: datetime) -> float:
    st = status(offer, now)
    if st == "ending_soon":
        hours = max((offer.ends_at - now).total_seconds() / 3600.0, 0)
        return 1.0 - min(hours / 48.0, 1.0) * 0.4  # 1.0 -> 0.6
    if st == "active":
        return 0.4
    if st in ("open_ended",):
        return 0.3
    if st == "upcoming":
        return 0.25
    return 0.0


def recommend(
    offers: Iterable[Offer],
    lat: float,
    lon: float,
    now: datetime,
    interests: Optional[Dict[str, float]] = None,
    radius_m: float = 3000.0,
    query_category: Optional[str] = None,
    limit: int = 5,
) -> List[Recommendation]:
    """Score = distance 0.45 + urgency 0.30 + interest 0.20 + confidence 0.05.

    Expired offers and anything beyond the radius (when it has a location)
    are never returned. `query_category` hard-filters when the user asked
    for something specific ("coffee deals").
    """
    interests = interests or {}
    out: List[Recommendation] = []
    for o in offers:
        st = status(o, now)
        if st == "expired":
            continue
        if query_category and o.category != query_category:
            continue
        d = None
        if o.lat is not None and o.lon is not None:
            d = haversine_m(lat, lon, o.lat, o.lon)
            if d > radius_m:
                continue
        ds = _distance_score(d, radius_m)
        us = _urgency_score(o, now)
        interest = min(max(interests.get(o.category, 0.0), 0.0), 1.0)
        score = 0.45 * ds + 0.30 * us + 0.20 * interest + 0.05 * o.confidence
        reasons = []
        if d is not None and d < 1000:
            reasons.append("close by")
        if st == "ending_soon":
            reasons.append("ending soon")
        if interest >= 0.5:
            reasons.append("matches your interests")
        out.append(Recommendation(o, round(score, 4), d, time_left_label(o, now), reasons))
    out.sort(key=lambda r: (-r.score, r.distance_m if r.distance_m is not None else 1e12))
    return out[:limit]
