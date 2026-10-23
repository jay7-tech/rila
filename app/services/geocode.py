"""Geocoding through OpenStreetMap Nominatim, cached in the database.

Nominatim's public service allows 1 request per second and requires a real
User-Agent, so every lookup is cached and calls are spaced out. For heavy
use, point NOMINATIM_URL at a self-hosted instance.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Callable, List, Optional, Tuple

import httpx
from rapidfuzz import fuzz
from sqlalchemy.orm import Session

from ..models import GeoCache

NOMINATIM_URL = os.environ.get("NOMINATIM_URL", "https://nominatim.openstreetmap.org/search")
USER_AGENT = os.environ.get("NOMINATIM_USER_AGENT", "Rilla/0.1 (contact: set NOMINATIM_USER_AGENT)")
MIN_MATCH_SCORE = 65
MIN_GAP_SECONDS = 1.1

_lock = threading.Lock()
_last_call = 0.0

GeoResult = Tuple[Optional[float], Optional[float], str]


def _fetch(query: str, limit: int = 3) -> List[dict]:
    """One HTTP call, rate-limited. Replaced in tests."""
    global _last_call
    with _lock:
        wait = MIN_GAP_SECONDS - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()
    resp = httpx.get(
        NOMINATIM_URL,
        params={"q": query, "format": "json", "limit": limit},
        headers={"User-Agent": USER_AGENT},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def _cache_get(session: Session, key: str) -> Optional[GeoResult]:
    row = session.get(GeoCache, key)
    return (row.lat, row.lon, row.status) if row else None


def _cache_put(session: Session, key: str, result: GeoResult) -> None:
    if result[2] == "error":  # never cache failures
        return
    session.merge(GeoCache(query=key, lat=result[0], lon=result[1], status=result[2]))
    session.flush()


def geocode_place(
    session: Session, name: str, where: str, fetch: Callable[[str], List[dict]] = None
) -> GeoResult:
    """Find a named place near `where` (area and/or city).

    Returns (lat, lon, status): confirmed | no_candidates | rejected | error.
    'rejected' means candidates existed but none resembled the name, which is
    the signal that the name was likely misheard or invented.
    """
    if not name or not where:
        return None, None, "error"
    key = f"place|{name.lower().strip()}|{where.lower().strip()}"
    cached = _cache_get(session, key)
    if cached:
        return cached
    try:
        results = (fetch or _fetch)(f"{name}, {where}")
    except Exception:
        return None, None, "error"
    if not results:
        out: GeoResult = (None, None, "no_candidates")
    else:
        best, best_score = None, 0.0
        for r in results:
            primary = r.get("display_name", "").split(",")[0].strip()
            score = fuzz.ratio(name.lower(), primary.lower())
            if score > best_score:
                best, best_score = r, score
        if best is None or best_score < MIN_MATCH_SCORE:
            out = (None, None, "rejected")
        else:
            out = (float(best["lat"]), float(best["lon"]), "confirmed")
    _cache_put(session, key, out)
    return out


def geocode_area(session: Session, where: str, fetch: Callable[[str], List[dict]] = None) -> GeoResult:
    """Centre point of an area or city, used as an approximate pin."""
    if not where:
        return None, None, "error"
    key = f"area|{where.lower().strip()}"
    cached = _cache_get(session, key)
    if cached:
        return cached
    try:
        results = (fetch or _fetch)(where)
    except Exception:
        return None, None, "error"
    if not results:
        out: GeoResult = (None, None, "no_candidates")
    else:
        out = (float(results[0]["lat"]), float(results[0]["lon"]), "approx")
    _cache_put(session, key, out)
    return out
