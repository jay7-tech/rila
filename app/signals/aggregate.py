"""Combine many signals into per-area, per-type consensus with a trust label."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, List, Tuple

from .freshness import MIN_FRESHNESS, freshness
from .models import Signal

# A claim is "confirmed" when this many *independent* sources agree
# and their combined fresh weight is high enough.
MIN_SOURCES_CONFIRMED = 2
MIN_WEIGHT_CONFIRMED = 1.0


@dataclass
class Consensus:
    area_key: str
    signal_type: str
    value: str
    severity: int | None
    status: str  # "confirmed" | "unconfirmed"
    n_sources: int  # independent sources agreeing with `value`
    weight: float
    newest: datetime


def normalise_value(value: str) -> str:
    return " ".join(value.lower().split())


def build_consensus(signals: Iterable[Signal], now: datetime) -> List[Consensus]:
    """Group by (area, type); the value with the most fresh weight wins.

    Sources are counted once per value, so one creator posting five reels
    cannot confirm their own claim.
    """
    groups: Dict[Tuple[str, str], Dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for s in signals:
        w = freshness(s.signal_type, s.observed_at, now) * s.confidence
        if freshness(s.signal_type, s.observed_at, now) < MIN_FRESHNESS:
            continue
        groups[(s.area_key, s.signal_type)][normalise_value(s.value)].append((s, w))

    results: List[Consensus] = []
    for (area_key, signal_type), by_value in groups.items():
        best_value, best_items = max(
            by_value.items(), key=lambda kv: sum(w for _, w in kv[1])
        )
        # one vote per source: keep each source's strongest weight
        per_source: Dict[str, float] = {}
        for s, w in best_items:
            per_source[s.source_id] = max(per_source.get(s.source_id, 0.0), w)
        weight = sum(per_source.values())
        n_sources = len(per_source)
        status = (
            "confirmed"
            if n_sources >= MIN_SOURCES_CONFIRMED and weight >= MIN_WEIGHT_CONFIRMED
            else "unconfirmed"
        )
        severities = [s.severity for s, _ in best_items if s.severity is not None]
        results.append(
            Consensus(
                area_key=area_key,
                signal_type=signal_type,
                value=best_value,
                severity=max(severities) if severities else None,
                status=status,
                n_sources=n_sources,
                weight=round(weight, 3),
                newest=max(s.observed_at for s, _ in best_items),
            )
        )
    results.sort(key=lambda c: (c.area_key, c.signal_type))
    return results
