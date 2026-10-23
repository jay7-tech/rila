"""The pipeline for one reel: ingest, extract, locate, de-duplicate, save."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, List, Optional

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Item, Signal, User
from ..recs.ranking import haversine_m
from ..signals.freshness import resolve_observed_at
from . import users as users_svc
from .embeddings import item_text
from .extract import ExtractionResult, ExtractedItem, parse_dt, parse_start

log = logging.getLogger(__name__)
DUP_RATIO = 85
DUP_RADIUS_M = 500


@dataclass
class ProcessResult:
    items: List[Item] = field(default_factory=list)
    duplicates: List[Item] = field(default_factory=list)
    signals: List[Signal] = field(default_factory=list)
    rejected: List[str] = field(default_factory=list)
    creator: Optional[str] = None


def norm(text: Optional[str]) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (text or "").lower())).strip()


def area_key(city: Optional[str], area: Optional[str]) -> str:
    c, a = norm(city), norm(area)
    return f"{c}|{a}" if a else f"{c}|{c}"


def _is_duplicate(new: Item, existing: List[Item]) -> Optional[Item]:
    for ex in existing:
        if fuzz.ratio(ex.normalized, new.normalized) <= DUP_RATIO:
            continue
        if new.lat is not None and ex.lat is not None:
            if haversine_m(new.lat, new.lon, ex.lat, ex.lon) < DUP_RADIUS_M:
                return ex
        else:
            return ex
    return None


def process_url(
    session: Session,
    user: User,
    url: str,
    now: datetime,
    ingest_fn: Callable,
    extract_fn: Callable,
    geocode_place_fn: Callable,
    geocode_area_fn: Callable,
    embed_fn: Optional[Callable] = None,
) -> ProcessResult:
    """Run the full pipeline. All I/O is passed in so it can be tested offline."""
    ing = ingest_fn(url)
    ex: ExtractionResult = extract_fn(ing.caption, ing.transcript, now, ing.posted_at)
    out = ProcessResult(creator=ing.creator)

    existing = list(session.scalars(select(Item).where(Item.user_id == user.id)))

    for it in ex.items:
        lat = lon = None
        geo = "none"
        where = ", ".join(x for x in (it.area, it.city) if x)
        if it.kind in ("place", "offer") and where:
            name = it.venue or it.title
            if name:
                lat, lon, st = geocode_place_fn(session, name, where)
                if st == "rejected":
                    out.rejected.append(name)
                    continue  # name did not match anything real nearby
                if st == "confirmed":
                    geo = "confirmed"
                else:
                    lat, lon, st2 = geocode_area_fn(session, where)
                    geo = "approx" if st2 == "approx" else "none"
                    if geo == "none":
                        lat = lon = None

        item = Item(
            user_id=user.id,
            kind=it.kind,
            category=it.category,
            title=it.title,
            normalized=norm(it.venue or it.title) if it.kind != "offer" else norm(f"{it.venue or ''} {it.title}"),
            venue=it.venue,
            city=it.city,
            area=it.area,
            summary=it.summary,
            price_text=it.price_text,
            duration_text=it.duration_text,
            starts_at=parse_start(it.starts_at),
            ends_at=parse_dt(it.ends_at),
            lat=lat,
            lon=lon,
            geo_status=geo,
            source_url=url,
            creator=ing.creator,
            confidence=it.confidence,
            created_at=now,
        )
        dup = _is_duplicate(item, existing)
        if dup:
            if item.ends_at and (dup.ends_at is None or item.ends_at > dup.ends_at):
                dup.ends_at = item.ends_at  # newer reel extends the offer
            out.duplicates.append(dup)
            continue
        session.add(item)
        session.flush()
        existing.append(item)
        out.items.append(item)
        users_svc.bump_interest(user, item.category)

    if embed_fn and out.items:
        try:
            for it, vec in zip(out.items, embed_fn([item_text(i) for i in out.items])):
                it.embedding = vec
        except Exception:  # search quality degrades; saving must not fail
            log.exception("embedding failed; items saved without vectors")

    source_id = (ing.creator or url)[:300]
    for s in ex.signals:
        sig = Signal(
            signal_type=s.signal_type,
            area_key=area_key(s.city, s.area_name),
            city=s.city,
            area_name=s.area_name,
            value=s.value.strip(),
            severity=s.severity,
            summary=s.summary,
            confidence=s.confidence,
            observed_at=resolve_observed_at(s.time_hint, ing.posted_at, now),
            source_id=source_id,
            source_url=url,
            user_id=user.id,
            created_at=now,
        )
        session.add(sig)
        out.signals.append(sig)
    session.flush()
    return out
