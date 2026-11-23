"""Plan an outing: pick the best nearby saved places and order them into a route that fits the time available.

This is the orienteering problem (choose stops to maximise total value within a time budget, with time windows),
which is NP-hard, so we use the standard practical heuristic:

1. value of a stop = its recommendation score (distance, urgency, learned interest, confidence);
2. cheapest insertion: repeatedly add the stop with the best value per extra minute that keeps the whole route
   feasible (inside the time budget and inside every offer's validity window);
3. 2-opt: reverse parts of the route while that makes it shorter and still feasible.

Travel time is straight-line distance x a detour factor / speed. This is an estimate, not a routing engine, and the
output says so. Pure functions: tested without a database or network.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from .offers import Offer
from .ranking import Recommendation, haversine_m

DETOUR = 1.35  # roads are longer than straight lines
WALK_KMH, DRIVE_KMH = 4.5, 18.0  # 18 km/h is a realistic Bengaluru city average
WALK_MAX_M = 1200.0  # in "auto" mode a hop shorter than this is walked
DWELL_MIN = {"food": 45, "attraction": 60, "event": 90, "store": 25, "education": 60, "service": 20, "scheme": 20}
DEFAULT_DWELL = 30
MAX_WAIT_MIN = 30  # waiting longer than this for an offer to start is not a plan
CLOSING_MARGIN_MIN = 15  # arrive at least this long before an offer ends
MAX_STOPS = 6


def travel_minutes(a: Tuple[float, float], b: Tuple[float, float], mode: str = "auto") -> float:
    d = haversine_m(a[0], a[1], b[0], b[1]) * DETOUR
    if mode == "walk" or (mode == "auto" and d <= WALK_MAX_M):
        speed = WALK_KMH
    else:
        speed = DRIVE_KMH
    return d / 1000.0 / speed * 60.0


@dataclass
class Stop:
    offer: Offer
    arrive: datetime
    depart: datetime
    travel_min: float
    wait_min: float
    dwell_min: int
    value: float


@dataclass
class Plan:
    stops: List[Stop] = field(default_factory=list)
    total_minutes: float = 0.0
    travel_minutes: float = 0.0
    value: float = 0.0
    considered: int = 0
    skipped: int = 0  # candidates that did not fit
    mode: str = "auto"


def dwell_for(offer: Offer) -> int:
    return DWELL_MIN.get(offer.category, DEFAULT_DWELL)


def simulate(order: Sequence[Recommendation], start: Tuple[float, float], now: datetime, budget_min: float,
             mode: str) -> Optional[List[Stop]]:
    """Walk the route in order. Returns the timed stops, or None if any stop or the budget is infeasible."""
    t, here, out = now, start, []
    for rec in order:
        o = rec.offer
        if o.lat is None or o.lon is None:
            return None
        tm = travel_minutes(here, (o.lat, o.lon), mode)
        arrive = t + timedelta(minutes=tm)
        wait = 0.0
        if o.starts_at and arrive < o.starts_at:
            wait = (o.starts_at - arrive).total_seconds() / 60.0
            if wait > MAX_WAIT_MIN:
                return None
            arrive = o.starts_at
        if o.ends_at and arrive > o.ends_at - timedelta(minutes=CLOSING_MARGIN_MIN):
            return None
        dwell = dwell_for(o)
        depart = arrive + timedelta(minutes=dwell)
        if (depart - now).total_seconds() / 60.0 > budget_min:
            return None
        out.append(Stop(o, arrive, depart, round(tm, 1), round(wait, 1), dwell, rec.score))
        t, here = depart, (o.lat, o.lon)
    return out


def _duration(stops: Optional[List[Stop]], now: datetime) -> float:
    return float("inf") if not stops else (stops[-1].depart - now).total_seconds() / 60.0


def plan_route(recs: Sequence[Recommendation], lat: float, lon: float, now: datetime, budget_min: float = 180.0,
               mode: str = "auto", max_stops: int = MAX_STOPS) -> Plan:
    """Best-effort route over located recommendations. Unlocated items cannot be routed and are skipped."""
    candidates = [r for r in recs if r.offer.lat is not None and r.offer.lon is not None]
    start = (lat, lon)
    order: List[Recommendation] = []
    remaining = list(candidates)

    while remaining and len(order) < max_stops:
        base = _duration(simulate(order, start, now, budget_min, mode), now) if order else 0.0
        best: Optional[Tuple[float, int, int, float]] = None  # ratio, candidate index, position, new duration
        for ci, cand in enumerate(remaining):
            for pos in range(len(order) + 1):
                trial = order[:pos] + [cand] + order[pos:]
                stops = simulate(trial, start, now, budget_min, mode)
                if stops is None:
                    continue
                dur = _duration(stops, now)
                ratio = cand.score / max(dur - base, 1.0)  # value gained per extra minute
                if best is None or ratio > best[0]:
                    best = (ratio, ci, pos, dur)
        if best is None:
            break
        _, ci, pos, _ = best
        order.insert(pos, remaining.pop(ci))

    order = _two_opt(order, start, now, budget_min, mode)
    stops = simulate(order, start, now, budget_min, mode) or []
    travel = sum(s.travel_min for s in stops)
    return Plan(
        stops=stops, total_minutes=round(_duration(stops, now), 1) if stops else 0.0, travel_minutes=round(travel, 1),
        value=round(sum(s.value for s in stops), 3), considered=len(recs), skipped=len(recs) - len(stops), mode=mode,
    )


def _two_opt(order: List[Recommendation], start, now, budget_min, mode) -> List[Recommendation]:
    """Reverse segments while it shortens the total time and keeps every window satisfied."""
    best = list(order)
    best_d = _duration(simulate(best, start, now, budget_min, mode), now)
    improved = True
    while improved and len(best) > 2:
        improved = False
        for i in range(len(best) - 1):
            for j in range(i + 1, len(best)):
                trial = best[:i] + best[i:j + 1][::-1] + best[j + 1:]
                d = _duration(simulate(trial, start, now, budget_min, mode), now)
                if d + 1e-9 < best_d:
                    best, best_d, improved = trial, d, True
    return best


def plan_to_dict(plan: Plan) -> dict:
    return {
        "stops": [
            {"id": s.offer.id, "title": s.offer.title, "category": s.offer.category, "lat": s.offer.lat, "lon": s.offer.lon,
             "arrive": s.arrive.isoformat(), "depart": s.depart.isoformat(), "travel_min": s.travel_min,
             "wait_min": s.wait_min, "stay_min": s.dwell_min, "ends_at": s.offer.ends_at.isoformat() if s.offer.ends_at else None}
            for s in plan.stops
        ],
        "total_minutes": plan.total_minutes, "travel_minutes": plan.travel_minutes, "value": plan.value,
        "considered": plan.considered, "skipped": plan.skipped, "mode": plan.mode,
        "note": "Times are estimates from straight-line distance, not live traffic.",
    }
