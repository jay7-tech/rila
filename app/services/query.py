"""Read-side logic shared by the API and the Telegram bot."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .. import db as dbmod
from ..models import Event, Item, Signal, User
from ..recs import learner, reliability, retrieval, taste
from ..recs.offers import Offer, status, time_left_label
from ..recs import planner
from ..recs.ranking import haversine_m, recommend
from ..signals.aggregate import build_consensus
from ..signals.briefing import format_briefing
from ..signals.freshness import age_label
from ..signals.models import Signal as SignalModel
from . import creators as creators_svc
from . import embeddings
from . import places as places_svc
from .process import norm

SIGNAL_WINDOW = timedelta(hours=72)
SEMANTIC_MAX_DISTANCE = 0.55  # cosine distance; larger means less related
KEYWORD_BONUS = 0.3


def item_to_offer(item: Item) -> Offer:
    return Offer(
        id=item.id, title=item.title, category=item.category, city=item.city, lat=item.lat, lon=item.lon,
        summary=item.summary, price_text=item.price_text, starts_at=item.starts_at, ends_at=item.ends_at,
        duration_text=item.duration_text, source_url=item.source_url, confidence=item.confidence,
        saved_at=item.created_at, creator=item.creator,
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
        "venue_en": item.venue_en, "evidence": item.evidence, "evidence_source": item.evidence_source,
        "evidence_at": item.evidence_at, "grounded": item.grounded,
    }


def _taste_dict(i: Item) -> dict:
    return {"category": i.category, "kind": i.kind, "creator": i.creator, "area": i.area, "city": i.city,
            "price": i.price_text, "evidence_source": i.evidence_source, "title": i.title, "summary": i.summary}


def taste_model(user_items_by_id: dict, events: List[Event], now: datetime) -> taste.Taste:
    """Retrain the user's taste model from their event log (cheap, deterministic, nothing stored)."""
    ex = [(e.action, _taste_dict(user_items_by_id[e.item_id]), e.created_at) for e in events if e.item_id in user_items_by_id]
    return taste.train(ex, now)


def annotate(session: Session, user: User, now: datetime, rows: List[dict]) -> List[dict]:
    """Add a transparent reliability score to serialized items (needs creator trust and how many reels mention the place)."""
    if not rows:
        return rows
    trust = creators_svc.trust_map(session, user, now)
    mentions: dict = {}
    for i in user_items(session, user):
        k = places_svc.name_key(i)
        if k:
            mentions.setdefault(k, set()).add(i.document_id or -i.id)
    by_id = {i.id: i for i in user_items(session, user)}
    for r in rows:
        i = by_id.get(r["id"])
        if i is None:
            continue
        parts = reliability.components(i.grounded, i.evidence_source, None, i.geo_status or "none",
                                       trust.get(i.creator or "", 0.5), len(mentions.get(places_svc.name_key(i), ())) or 1)
        s = reliability.score(parts)
        r["reliability"] = {"score": s, "label": reliability.label(s), "parts": reliability.breakdown(parts)}
    return rows


def for_you(session: Session, user: User, now: datetime, lat: Optional[float] = None, lon: Optional[float] = None,
            limit: int = 12, radius_m: float = 8000) -> dict:
    """A feed ranked by learned taste p(like), the usual rank score (distance/urgency) and a small exploration bonus,
    then diversified with MMR so one venue or category does not fill the page."""
    items = {i.id: i for i in user_items(session, user) if status(item_to_offer(i), now) != "expired"}
    events = user_events(session, user)
    model = taste_model({i.id: i for i in user_items(session, user)}, events, now)
    dismissed = {e.item_id for e in events if e.action == "dismiss" and e.item_id}
    seen = {e.item_id for e in events if e.item_id}
    rank_score: dict = {}
    dist: dict = {}
    if lat is not None and lon is not None:
        offers = [item_to_offer(i) for i in items.values() if i.id not in dismissed]
        for r in recommend(offers, lat, lon, now, interests=learned_interests(user, events, now, {i.category for i in items.values()}),
                           radius_m=radius_m, limit=len(offers) or 1, trust=creators_svc.trust_map(session, user, now, events)):
            rank_score[r.offer.id] = r.score
            dist[r.offer.id] = r.distance_m
    cand = [i for i in items.values() if i.id not in dismissed and (not rank_score or i.id in rank_score)]
    rel: dict = {}
    why: dict = {}
    for i in cand:
        p = model.predict(_taste_dict(i)) if not model.cold else 0.5
        explore = 0.08 if i.id not in seen else 0.0
        rel[i.id] = (0.55 * p if not model.cold else 0.0) + 0.45 * rank_score.get(i.id, 0.3) + explore
        why[i.id] = [f"{'likes' if r['weight'] > 0 else 'less into'} {taste.feature_text(r['feature'])}" for r in model.explain(_taste_dict(i))] if not model.cold else []
        if explore:
            why[i.id].append("new to you")
    feats = {i.id: {"c:" + i.category, "v:" + (places_svc.name_key(i) or str(i.id)), "k:" + i.creator.lower() if i.creator else ""} - {""} for i in cand}
    venues = {i.id: places_svc.name_key(i) for i in cand}
    order = retrieval.mmr(rel, retrieval.item_similarity(feats, venues), limit, lam=0.75)
    rows = [serialize_item(items[i], now, dist.get(i), why[i]) for i in order]
    for r in rows:
        r["taste"] = None if model.cold else round(model.predict(_taste_dict(items[r["id"]])), 3)
    return {"items": annotate(session, user, now, rows), "cold": model.cold, "trained_on": model.n,
            "learned": {} if model.cold else {k: [dict(feature=taste.feature_text(x["feature"]), weight=x["weight"]) for x in v]
                                              for k, v in model.top_features().items()}}


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


def user_events(session: Session, user: User, limit: int = 2000) -> List[Event]:
    return list(session.scalars(select(Event).where(Event.user_id == user.id).order_by(Event.id.desc()).limit(limit)))


def learned_interests(user: User, events: List[Event], now: datetime, categories=()) -> dict:
    obs = [(e.action, e.category, e.created_at) for e in events]
    return learner.effective_interests(user.interests or {}, obs, now, categories)


def recommend_for_user(
    session: Session, user: User, lat: float, lon: float, now: datetime,
    radius_m: float = 3000, category: Optional[str] = None, limit: int = 10,
) -> List[dict]:
    items = {i.id: i for i in nearby_candidates(session, user, lat, lon, radius_m)}
    events = user_events(session, user)
    dismissed = {e.item_id for e in events if e.action == "dismiss" and e.item_id}
    offers = [item_to_offer(i) for i in items.values() if i.id not in dismissed]  # "not interested" means gone
    recs = recommend(
        offers, lat, lon, now,
        interests=learned_interests(user, events, now, {i.category for i in items.values()}),
        radius_m=radius_m, query_category=category, limit=limit, trust=creators_svc.trust_map(session, user, now, events),
    )
    return annotate(session, user, now, [serialize_item(items[r.offer.id], now, r.distance_m, r.reasons, r.label) for r in recs])


def plan_for_user(
    session: Session, user: User, lat: float, lon: float, now: datetime, minutes: float = 180.0,
    mode: str = "auto", category: Optional[str] = None, radius_m: float = 6000,
) -> dict:
    """Order the user's best nearby saved places into a route that fits `minutes`."""
    items = {i.id: i for i in nearby_candidates(session, user, lat, lon, radius_m)}
    events = user_events(session, user)
    dismissed = {e.item_id for e in events if e.action == "dismiss" and e.item_id}
    visited = {i.id for i in items.values() if i.visited}
    offers = [item_to_offer(i) for i in items.values() if i.id not in dismissed and i.id not in visited]
    recs = recommend(
        offers, lat, lon, now, interests=learned_interests(user, events, now, {i.category for i in items.values()}),
        radius_m=radius_m, query_category=category, limit=40, trust=creators_svc.trust_map(session, user, now, events),
    )
    out = planner.plan_to_dict(planner.plan_route(recs, lat, lon, now, minutes, mode))
    out["candidates"] = sum(1 for r in recs if r.offer.lat is not None)  # saved places within reach, before fitting the time
    out["radius_m"] = radius_m
    out["unpinned"] = sum(1 for r in recs if r.offer.lat is None)
    for stop in out["stops"]:
        it = items[stop["id"]]
        stop.update(venue=it.venue, area=it.area, price=it.price_text)
    return out


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
    """BM25F (words, typo tolerant) + dense vectors (meaning), fused with RRF and diversified with MMR. Expired offers are hidden."""
    items = {i.id: i for i in user_items(session, user) if status(item_to_offer(i), now) != "expired"}
    if not items or not norm(query):
        return []
    dist: dict = {}
    try:
        vec = embeddings.embed_query(query)
    except Exception:
        vec = None  # model unavailable: words only
    if vec is not None and dbmod.is_postgres(session.get_bind()):
        rows = session.execute(
            select(Item.id, Item.embedding.cosine_distance(vec).label("dist"))
            .where(Item.user_id == user.id, Item.embedding.is_not(None)).order_by("dist").limit(limit * 8)
        ).all()
        dist = {r[0]: float(r[1]) for r in rows}
    elif vec is not None:
        dist = {i.id: embeddings.cosine_distance(vec, i.embedding) for i in items.values() if i.embedding}
    docs = {
        i.id: {"venue": i.venue_en or i.venue or "", "title": i.title, "category": i.category, "area": i.area or "", "city": i.city or "",
               "price": i.price_text or "", "summary": i.summary or ""}
        for i in items.values()
    }
    venues = {i.id: norm(i.venue_en or i.venue or "") for i in items.values()}
    hits = retrieval.search(docs, query, dist, k=limit, dense_max=SEMANTIC_MAX_DISTANCE, venues=venues)
    out = []
    for h in hits:
        i = items[h["id"]]
        dm = haversine_m(lat, lon, i.lat, i.lon) if None not in (lat, lon, i.lat, i.lon) else None
        why = (["matches " + ", ".join(h["terms"])] if h["terms"] else []) + (["similar meaning"] if h["semantic"] else [])
        out.append(serialize_item(i, now, dm, reasons=why))
    return annotate(session, user, now, out)


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
