"""Render consensus signals as a Telegram-friendly destination briefing."""
from __future__ import annotations

from datetime import datetime
from typing import List

from .aggregate import Consensus
from .freshness import age_label

TYPE_LABEL = {
    "crowd": "Crowd",
    "weather": "Weather on the ground",
    "hazard": "Hazard",
    "traffic": "Traffic",
    "opening": "Open",
    "closure": "Closed",
    "event": "Event",
    "deal": "Deal",
    "price": "Price",
}

TYPE_ORDER = ["hazard", "weather", "crowd", "traffic", "closure", "opening", "event", "deal", "price"]


def format_briefing(destination: str, items: List[Consensus], now: datetime) -> str:
    if not items:
        return (
            f"No recent reports for {destination} yet.\n"
            "If you see a fresh reel about it, send it to me and I'll add it."
        )

    ordered = sorted(
        items,
        key=lambda c: (TYPE_ORDER.index(c.signal_type) if c.signal_type in TYPE_ORDER else 99, c.area_key),
    )
    lines = [f"{destination}: what locals are reporting"]
    for c in ordered:
        label = TYPE_LABEL.get(c.signal_type, c.signal_type.title())
        area = c.area_key.split("|", 1)[-1].strip()
        where = f" ({area})" if area and area != c.area_key.split("|", 1)[0] else ""
        trust = (
            f"{c.n_sources} reports"
            if c.status == "confirmed"
            else "1 report, unconfirmed"
            if c.n_sources == 1
            else f"{c.n_sources} reports, weak"
        )
        lines.append(f"- {label}{where}: {c.value} [{trust}, {age_label(c.newest, now)}]")
    lines.append("Reports come from public reels sent by users. Check official sources for safety.")
    return "\n".join(lines)
