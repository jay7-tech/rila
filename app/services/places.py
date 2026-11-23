"""Places: the same venue seen in many reels, merged into one record with everything known about it.

Every saved item is one mention of a venue. When three creators post about the same cafe, the user
should see one place with three reels behind it, its prices, its live offers and the best proof, not
three unrelated cards. This is entity resolution: decide which mentions refer to the same real place.

Two mentions are the same place when their names are close AND their locations do not disagree:
both pinned and within MERGE_RADIUS_M, or at least one unpinned and the city/area do not conflict.
Pure functions over any object with the Item attributes, so they are tested without a database.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence

from rapidfuzz import fuzz

from ..core.text import normalize
from ..recs.ranking import haversine_m

NAME_SIMILARITY = 88  # token_sort_ratio, 0-100
MERGE_RADIUS_M = 600.0
GENERIC = {"the", "restaurant", "resturant", "hotel", "cafe", "bar", "and", "of"}


def name_key(item) -> str:
    """Comparable form of a venue name: English spelling preferred, generic words removed."""
    words = normalize(item.venue_en or item.venue or "").split()
    core = [w for w in words if w not in GENERIC]
    return " ".join(core or words)


def _same_name(a: str, b: str) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    return fuzz.token_sort_ratio(a, b) >= NAME_SIMILARITY


def _locations_agree(a, b) -> bool:
    pinned = lambda i: i.lat is not None and i.lon is not None and getattr(i, "geo_status", "") == "confirmed"
    if pinned(a) and pinned(b):
        return haversine_m(a.lat, a.lon, b.lat, b.lon) <= MERGE_RADIUS_M
    for field_name in ("city", "area"):
        x, y = normalize(getattr(a, field_name, None)), normalize(getattr(b, field_name, None))
        if x and y and fuzz.ratio(x, y) < 80:
            return False  # known and different
    return True


@dataclass
class Place:
    name: str
    key: str
    city: Optional[str] = None
    area: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    geo: str = "none"
    mentions: int = 0
    reels: int = 0
    creators: List[str] = field(default_factory=list)
    categories: List[str] = field(default_factory=list)
    prices: List[str] = field(default_factory=list)
    summaries: List[str] = field(default_factory=list)
    item_ids: List[int] = field(default_factory=list)
    live_offers: List[dict] = field(default_factory=list)
    verified: int = 0  # mentions whose quote was found in the video
    confidence: float = 0.0
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    best_evidence: Optional[dict] = None
    visited: bool = False


def cluster(items: Iterable) -> List[List]:
    """Group mentions of the same venue (union-find over pairwise name and location agreement)."""
    items = [i for i in items if getattr(i, "kind", "") in ("place", "offer") and name_key(i)]
    parent = list(range(len(items)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    keys = [name_key(i) for i in items]
    for a in range(len(items)):
        for b in range(a + 1, len(items)):
            if find(a) != find(b) and _same_name(keys[a], keys[b]) and _locations_agree(items[a], items[b]):
                parent[find(a)] = find(b)
    groups: Dict[int, List] = {}
    for idx, item in enumerate(items):
        groups.setdefault(find(idx), []).append(item)
    return list(groups.values())


def _unique(values: Iterable[Optional[str]], limit: int) -> List[str]:
    seen, out = set(), []
    for v in values:
        v = (v or "").strip()
        if v and v.lower() not in seen:
            seen.add(v.lower())
            out.append(v)
    return out[:limit]


def _noisy_or(confidences: Sequence[float]) -> float:
    """Probability at least one independent mention is right: 1 - prod(1 - c). More reels, more trust."""
    p = 1.0
    for c in confidences:
        p *= 1.0 - min(max(c, 0.0), 0.99)
    return round(1.0 - p, 3)


def build_place(group: Sequence, now: datetime) -> Place:
    names = Counter((i.venue_en or i.venue or "").strip() for i in group)
    top = max(names.items(), key=lambda kv: (kv[1], len(kv[0])))[0]  # most common spelling, longest on ties
    confirmed = [i for i in group if i.lat is not None and getattr(i, "geo_status", "") == "confirmed"]
    pinned = confirmed or [i for i in group if i.lat is not None]
    reel_conf: Dict[str, float] = {}
    for i in group:  # one vote per reel, so a creator posting the same reel twice does not inflate trust
        reel_conf[i.source_url or str(i.id)] = max(reel_conf.get(i.source_url or str(i.id), 0.0), i.confidence or 0.0)
    graded = sorted((i for i in group if i.grounded and i.evidence), key=lambda i: -(i.confidence or 0.0))
    best = graded[0] if graded else None
    created = [i.created_at for i in group if i.created_at]
    live = [
        {"id": i.id, "title": i.title, "ends_at": i.ends_at.isoformat() if i.ends_at else None, "price": i.price_text}
        for i in group if i.kind == "offer" and not (i.ends_at and i.ends_at < now)
    ]
    return Place(
        name=top, key=name_key(group[0]),
        city=next((i.city for i in group if i.city), None), area=next((i.area for i in group if i.area), None),
        lat=sum(i.lat for i in pinned) / len(pinned) if pinned else None,
        lon=sum(i.lon for i in pinned) / len(pinned) if pinned else None,
        geo="confirmed" if confirmed else ("approx" if pinned else "none"),
        mentions=len(group), reels=len(reel_conf),
        creators=_unique((i.creator for i in group), 5), categories=_unique((i.category for i in group), 3),
        prices=_unique((i.price_text for i in group), 5), summaries=_unique((i.summary for i in group), 3),
        item_ids=[i.id for i in group], live_offers=live,
        verified=sum(1 for i in group if i.grounded), confidence=_noisy_or(list(reel_conf.values())),
        first_seen=min(created) if created else None, last_seen=max(created) if created else None,
        best_evidence=None if best is None else {
            "quote": best.evidence, "source": best.evidence_source, "at": best.evidence_at, "url": best.source_url},
        visited=any(getattr(i, "visited", False) for i in group),
    )


def resolve_places(items: Iterable, now: datetime) -> List[Place]:
    places = [build_place(g, now) for g in cluster(items)]
    places.sort(key=lambda p: (-p.reels, -p.confidence, p.name.lower()))
    return places


def place_to_dict(p: Place, distance_m: Optional[float] = None) -> dict:
    return {
        "name": p.name, "city": p.city, "area": p.area, "lat": p.lat, "lon": p.lon, "geo": p.geo,
        "mentions": p.mentions, "reels": p.reels, "creators": p.creators, "categories": p.categories,
        "prices": p.prices, "summaries": p.summaries, "item_ids": p.item_ids, "live_offers": p.live_offers,
        "verified": p.verified, "confidence": p.confidence, "visited": p.visited,
        "first_seen": p.first_seen.isoformat() if p.first_seen else None,
        "last_seen": p.last_seen.isoformat() if p.last_seen else None,
        "best_evidence": p.best_evidence, "distance_m": round(distance_m) if distance_m is not None else None,
    }


def list_places(items: Iterable, now: datetime, lat: Optional[float] = None, lon: Optional[float] = None,
                q: Optional[str] = None, limit: int = 50) -> List[dict]:
    """Places for one user, optionally filtered by text and ordered nearest first when a location is given."""
    terms = normalize(q).split() if q else []
    out = []
    for p in resolve_places(items, now):
        hay = normalize(" ".join([p.name, p.city or "", p.area or "", " ".join(p.categories), " ".join(p.prices), " ".join(p.summaries)]))
        if terms and not all(t in hay for t in terms):
            continue
        d = haversine_m(lat, lon, p.lat, p.lon) if None not in (lat, lon, p.lat, p.lon) else None
        out.append((d, p))
    if lat is not None and lon is not None:
        out.sort(key=lambda t: (t[0] is None, t[0] if t[0] is not None else 0.0, -t[1].reels))
    return [place_to_dict(p, d) for d, p in out[:limit]]
