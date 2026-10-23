"""Signal types for Rilla's "what is it like there right now" engine.

A *signal* is a short-lived, timestamped observation about an area
(crowd level, weather on the ground, a hazard, an opening...) extracted
from a reel. Unlike a saved place, a signal decays with age.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

SignalType = Literal[
    "crowd", "weather", "hazard", "traffic", "opening", "closure", "event", "deal", "price"
]

SIGNAL_TYPES: tuple = (
    "crowd", "weather", "hazard", "traffic", "opening", "closure", "event", "deal", "price",
)


class ExtractedSignal(BaseModel):
    """What the LLM returns for one observation in a reel."""

    signal_type: SignalType
    area_name: Optional[str] = None  # e.g. "Om Beach"
    city: Optional[str] = None  # e.g. "Gokarna"
    value: str = Field(description="Short normalised value, e.g. 'packed', 'heavy rain', 'open'")
    severity: Optional[int] = Field(default=None, ge=0, le=3, description="0 calm .. 3 extreme")
    summary: Optional[str] = None
    time_hint: Optional[str] = Field(
        default=None, description="Relative time words from the reel: today, yesterday, this morning..."
    )
    confidence: float = Field(ge=0.0, le=1.0)


class ExtractedSignals(BaseModel):
    signals: List[ExtractedSignal]


class Signal(BaseModel):
    """A stored signal with provenance."""

    signal_type: SignalType
    area_key: str  # normalised "city|area" key used for grouping
    value: str
    severity: Optional[int] = None
    confidence: float
    observed_at: datetime
    source_id: str  # reel URL or creator id; used to count *independent* sources
