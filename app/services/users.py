from __future__ import annotations

import secrets
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import User


def new_token() -> str:
    return secrets.token_urlsafe(24)


def get_by_token(session: Session, token: str) -> Optional[User]:
    if not token:
        return None
    return session.scalar(select(User).where(User.token == token))


def get_by_telegram(session: Session, telegram_id: int) -> Optional[User]:
    return session.scalar(select(User).where(User.telegram_id == telegram_id))


def create_user(session: Session, telegram_id: Optional[int], name: str) -> User:
    user = User(telegram_id=telegram_id, name=name or "", token=new_token(), interests={})
    session.add(user)
    session.flush()
    return user


def bump_interest(user: User, category: str, amount: float = 0.1) -> None:
    """Saving things in a category slowly raises that interest (cap 1.0)."""
    interests = dict(user.interests or {})
    interests[category] = round(min(interests.get(category, 0.0) + amount, 1.0), 3)
    user.interests = interests  # reassign so SQLAlchemy sees the change


def set_location(user: User, lat: float, lon: float) -> None:
    user.last_lat, user.last_lon = lat, lon
