"""Database tables. Datetimes are stored as naive UTC."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from pgvector.sqlalchemy import Vector
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


EMBED_DIM = 384  # BAAI/bge-small-en-v1.5
# pgvector on Postgres; a JSON list on SQLite (local runs and fast tests).
EmbeddingType = Vector(EMBED_DIM).with_variant(JSON(), "sqlite")


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[Optional[int]] = mapped_column(BigInteger, unique=True, nullable=True)
    name: Mapped[str] = mapped_column(String(120), default="")
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    last_lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    last_lon: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    interests: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Item(Base):
    """Something saved from a reel: a place, an offer, or non-physical info."""

    __tablename__ = "items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="place")  # place | offer | info
    category: Mapped[str] = mapped_column(String(24), default="other")
    title: Mapped[str] = mapped_column(Text)
    normalized: Mapped[str] = mapped_column(Text, default="")
    venue: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    city: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    area: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    price_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    duration_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    starts_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    ends_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    lon: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    geo_status: Mapped[str] = mapped_column(String(16), default="none")  # confirmed | approx | none
    source_url: Mapped[str] = mapped_column(Text, default="")
    creator: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.8)
    embedding: Mapped[Optional[list]] = mapped_column(EmbeddingType, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    visited: Mapped[bool] = mapped_column(Boolean, default=False)

    __table_args__ = (Index("ix_items_user_geo", "user_id", "lat", "lon"),)


class Signal(Base):
    """Same-day observation (crowd, weather, hazard...). Shared across users."""

    __tablename__ = "signals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    signal_type: Mapped[str] = mapped_column(String(16))
    area_key: Mapped[str] = mapped_column(String(200), index=True)
    city: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    area_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    value: Mapped[str] = mapped_column(Text)
    severity: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float)
    observed_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    source_id: Mapped[str] = mapped_column(String(300))
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Job(Base):
    """A reel waiting to be processed."""

    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    url: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(12), default="queued", index=True)  # queued|running|done|failed
    chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)  # Telegram chat to notify
    result: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class GeoCache(Base):
    __tablename__ = "geocache"
    query: Mapped[str] = mapped_column(String(400), primary_key=True)
    lat: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    lon: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
