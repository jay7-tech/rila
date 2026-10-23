"""Read-side logic shared by the API and the Telegram bot."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .. import db as dbmod
from ..models import Item, Signal, User
from ..recs.offers import Offer, status, time_left_label
from ..recs.ranking import haversine_m, recommend
from ..signals.aggregate import build_consensus
from ..signals.briefing import format_briefing
from ..signals.freshness import age_label
from ..signals.models import Signal as SignalModel
from . import embeddings
from .process import norm

SIGNAL_WINDOW = timedelta(hours=72)
SEMANTIC_MAX_DISTANCE = 0.55  # cosine distance; larger means less related
KEYWORD_BONUS = 0.3


def item_to_offer(item: Item) -> Offer:
    return Offer(
        id=item.id, title=item.title, category=item.category, city=item.city, lat=item.lat, lon=item.lon,
        summary=item.summary, price_text=item.price_text, starts_at=item.starts_at, ends_at=item.ends_at,
        duration_text=item.duration_text, source_url=item.source_url, confidence=item.confidence,
        saved_at=item.created_at,
    )


def serialize_item(item: Item, now: datetime, distance_m: Optional[float] = None, reasons=None, label=None) -> dict:
    offer = item_to_offer(item)
    return {
        "id": item.id, "kind": item.kind, "category": item.category, "title": item.title,
        "venue": item.venue, "city": item.city, "area": item.area, "summary": item.summary,
        "price": item.price_text, "when": item.duration_text,
        "starts_at": item.starts_at.isoformat() if item.starts_at else None,
        "ends_at": item.ends_at.isoformat() if item.ends_at else None,
        "status": status(offer, now), "time_left": label or time_left_label(offer, now),
        "lat": item.lat, "lon": item.lon, "geo": item.geo_status,
        "distance_m": round(distance_m) if distance_m is not None else None,
        "reasons": reasons or [], "source_url": item.source_url, "creator": item.creator,
        "saved_at": item.created_at.isoformat(),
    }


def user_items(session: Session, user: User) -> List[Item]:
    return list(session.scalars(select(Item).where(Item.user_id == user.id)))


def nearby_candidates(session: Session, user: User, lat: float, lon: float, radius_m: float) -> List[Item]:
    """Items that could be recommended: within the radius, or with no location.

    On Postgres the radius test runs in PostGIS against a GiST index; on SQLite
    everything is returned and ranking applies the radius.
    """
    if not dbmod.is_postgres(session.get_bind()):
        return user_items(session, user)
    ids = session.execute(
        text(
            "SELECT id FROM items WHERE user_id = :u AND "
            "(geog IS NULL OR ST_DWithin(geog, ST_SetSRID(ST_MakePoint(:lon, :lat), 4326)::geography, :r))"
        ),
        {"u": user.id, "lat": lat, "lon": lon, "r": radius_m},
    ).scalars().all()
    if not ids:
        return []
    return list(session.scalars(select(Item).where(Item.id.in_(ids))))


def recommend_for_user(
    session: Session, user: User, lat: float, lon: float, now: datetime,
    radius_m: float = 3000, category: Optional[str] = None, limit: int = 10,
) -> List[dict]:
    items = {i.id: i for i in nearby_candidates(session, user, lat, lon, radius_m)}
    recs = recommend(
        [item_to_offer(i) for i in items.values()], lat, lon, now,
        interests=user.interests or {}, radius_m=radius_m, query_category=category, limit=limit,
    )
    return [serialize_item(items[r.offer.id], now, r.distance_m, r.reasons, r.label) for r in recs]


def _hay(i: Item) -> str:
    return norm(" ".join(filter(None, [i.title, i.venue, i.city, i.area, i.summary, i.category, i.price_text])))


def list_saved(
    session: Session, user: User, now: datetime, category: Optional[str] = None,
    q: Optional[str] = None, lat: Optional[float] = None, lon: Optional[float] = None,
    include_expired: bool = False,
) -> List[dict]:
    out = []
    terms = norm(q).split() if q else []
    for i in user_items(session, user):
        if status(item_to_offer(i), now) == "expired" and not include_expired:
            continue
        if category and i.category != category:
            continue
        if terms and not all(t in _hay(i) for t in terms):
            continue
        d = haversine_m(lat, lon, i.lat, i.lon) if None not in (lat, lon, i.lat, i.lon) else None
        out.append((d, i))
    out.sort(key=lambda p: (p[0] is None, p[0] if p[0] is not None else 0, -p[1].created_at.timestamp()))
    return [serialize_item(i, now, d) for d, i in out]


def search_saved(
    session: Session, user: User, query: str, now: datetime,
    lat: Optional[float] = None, lon: Optional[float] = None, limit: int = 10,
) -> List[dict]:
    """Hybrid search: meaning (embeddings) plus exact words. Expired offers are hidden."""
    terms = norm(query).split()
    scored = []
    vec = None
    try:
        vec = embeddings.embed_query(query)
    except Exception:
        vec = None  # model unavailable: fall back to keywords only
    if vec is not None and dbmod.is_postgres(session.get_bind()):
        rows = session.execute(
            select(Item, Item.embedding.cosine_distance(vec).label("dist"))
            .where(Item.user_id == user.id, Item.embedding.is_not(None))
            .order_by("dist").limit(limit * 5)
        ).all()
        dist = {r[0].id: float(r[1]) for r in rows}
    elif vec is not None:
        dist = {
            i.id: embeddings.cosine_distance(vec, i.embedding)
            for i in user_items(session, user) if i.embedding
        }
    else:
        dist = {}
    for i in user_items(session, user):
        if status(item_to_offer(i), now) == "expired":
            continue
        kw = bool(terms) and all(t in _hay(i) for t in terms)
        d = dist.get(i.id)
        if d is None and not kw:
            continue
        if d is not None and d > SEMANTIC_MAX_DISTANCE and not kw:
            continue
        score = (d if d is not None else 0.5) - (KEYWORD_BONUS if kw else 0.0)
        scored.append((score, i))
    scored.sort(key=lambda p: p[0])
    out = []
    for _, i in scored[:limit]:
        dm = haversine_m(lat, lon, i.lat, i.lon) if None not in (lat, lon, i.lat, i.lon) else None
        out.append(serialize_item(i, now, dm))
    return out


def briefing_for(session: Session, place: str, now: datetime) -> dict:
    key = norm(place)
    rows = session.scalars(select(Signal).where(Signal.observed_at >= now - SIGNAL_WINDOW))
    signals = [
        SignalModel(
            signal_type=r.signal_type, area_key=r.area_key, value=r.value, severity=r.severity,
            confidence=r.confidence, observed_at=r.observed_at, source_id=r.source_id,
        )
        for r in rows if key and key in r.area_key
    ]
    consensus = build_consensus(signals, now)
    return {
        "place": place,
        "text": format_briefing(place.title(), consensus, now),
        "signals": [
            {
                "type": c.signal_type, "area": c.area_key.split("|", 1)[-1], "value": c.value,
                "status": c.status, "sources": c.n_sources, "age": age_label(c.newest, now),
                "observed_at": c.newest.isoformat(), "severity": c.severity,
            }
            for c in consensus
        ],
    }
