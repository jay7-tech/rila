"""Database engine and sessions.

Postgres (PostGIS + pgvector) is the real target; its schema is managed by
Alembic. SQLite is supported for quick local runs and fast unit tests and
is created straight from the models.
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


SessionLocal = sessionmaker(autoflush=False, expire_on_commit=False)
_engine = None
_url = ""


def normalize_url(url: str) -> str:
    """Accept postgres:// and postgresql:// and always use the psycopg2 driver."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg2://" + url[len(prefix):]
    return url


def is_postgres(bind=None) -> bool:
    b = bind or _engine
    return b is not None and b.dialect.name == "postgresql"


def get_engine():
    return _engine


def init_db(url: str, migrate: bool = True):
    """Create the engine; on Postgres run Alembic migrations, on SQLite create tables."""
    global _engine, _url
    url = normalize_url(url)
    kwargs: dict = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in url or url in ("sqlite://", "sqlite:///"):
            kwargs["poolclass"] = StaticPool
        else:
            path = url.split("sqlite:///", 1)[-1]
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    else:
        kwargs.update(pool_size=10, max_overflow=10)
    _engine = create_engine(url, **kwargs)
    _url = url
    SessionLocal.configure(bind=_engine)
    from . import models  # noqa: F401  (register tables)

    if url.startswith("sqlite"):
        Base.metadata.create_all(_engine)
    elif migrate:
        run_migrations(url)
    return _engine


def run_migrations(url: str) -> None:
    from alembic import command
    from alembic.config import Config

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(cfg, "head")


def reset_postgres() -> None:
    """Test helper: empty every table (keeps schema)."""
    with _engine.begin() as c:
        c.execute(text("TRUNCATE users, items, signals, jobs, geocache RESTART IDENTITY CASCADE"))


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
