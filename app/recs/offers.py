"""Offers and listings extracted from reels (food deals, openings, classes, schemes).

Unlike live signals, an offer has an explicit validity window, so its
relevance is driven by time-left, not decay.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from pydantic import BaseModel, Field, model_validator


class Offer(BaseModel):
    id: int
    title: str
    category: str  # food, store, event, scheme, education, service, other
    city: Optional[str] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    summary: Optional[str] = None
    price_text: Optional[str] = None  # "Rs 299 combo", "50% off"
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    duration_text: Optional[str] = None  # "weekdays 3-6pm", "this weekend"
    source_url: str = ""
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    saved_at: Optional[datetime] = None

    @model_validator(mode="after")
    def _window_order(self):
        if self.starts_at and self.ends_at and self.ends_at < self.starts_at:
            raise ValueError("ends_at before starts_at")
        return self


def status(offer: Offer, now: datetime) -> str:
    """'upcoming' | 'active' | 'ending_soon' | 'expired' | 'open_ended'."""
    if offer.ends_at and now > offer.ends_at:
        return "expired"
    if offer.starts_at and now < offer.starts_at:
        return "upcoming"
    if offer.ends_at is None:
        return "open_ended"
    if offer.ends_at - now <= timedelta(hours=48):
        return "ending_soon"
    return "active"


def time_left_label(offer: Offer, now: datetime) -> str:
    st = status(offer, now)
    if st == "expired":
        return "expired"
    if st == "upcoming":
        return "starts " + offer.starts_at.strftime("%d %b")
    if offer.ends_at is None:
        return offer.duration_text or "no end date"
    secs = (offer.ends_at - now).total_seconds()
    if secs < 3600:
        return f"ends in {max(int(secs // 60), 1)} min"
    if secs < 48 * 3600:
        return f"ends in {int(secs // 3600)}h"
    return f"ends in {int(secs // 86400)}d"
