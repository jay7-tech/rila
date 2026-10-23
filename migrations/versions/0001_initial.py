"""Initial schema: tables, PostGIS geography column, pgvector index.

Revision ID: 0001
"""
from alembic import op

from app.db import Base
from app import models  # noqa: F401

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # Baseline: later migrations must be explicit; the first creates everything.
    Base.metadata.create_all(bind=op.get_bind())
    # Generated from lat/lon so the application only ever writes plain numbers.
    op.execute(
        """
        ALTER TABLE items ADD COLUMN geog geography(Point, 4326)
        GENERATED ALWAYS AS (
            CASE WHEN lat IS NOT NULL AND lon IS NOT NULL
                 THEN ST_SetSRID(ST_MakePoint(lon, lat), 4326)::geography END
        ) STORED
        """
    )
    op.execute("CREATE INDEX ix_items_geog ON items USING GIST (geog)")
    op.execute("CREATE INDEX ix_items_embedding ON items USING hnsw (embedding vector_cosine_ops)")
    op.execute("CREATE INDEX ix_signals_type_time ON signals (signal_type, observed_at DESC)")


def downgrade() -> None:
    for t in ("jobs", "signals", "items", "geocache", "users"):
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
