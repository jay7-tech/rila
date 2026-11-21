"""Documents (perceived timelines), evidence columns on items, job kinds.

Revision ID: 0002
Revises: 0001

Migration 0001 builds tables from the current models, so on a brand-new database these objects
already exist when 0002 runs. Every step therefore checks first and only adds what is missing.
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def _columns(insp, table):
    return {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    postgres = bind.dialect.name == "postgresql"  # SQLite (laptop runs without Docker) cannot add a foreign key afterwards
    if "documents" not in insp.get_table_names():
        op.create_table(
            "documents",
            sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False, index=True),
            sa.Column("kind", sa.String(8), nullable=False, server_default="video"),
            sa.Column("source_url", sa.Text, nullable=False, server_default=""),
            sa.Column("language", sa.String(16), nullable=True),
            sa.Column("duration", sa.Float, nullable=True),
            sa.Column("timeline", sa.JSON, nullable=False),
            sa.Column("meta", sa.JSON, nullable=False),
            sa.Column("created_at", sa.DateTime, nullable=False),
        )
    have = _columns(insp, "items")
    new_item_cols = [
        sa.Column("venue_en", sa.Text, nullable=True),
        sa.Column("evidence", sa.Text, nullable=True),
        sa.Column("evidence_source", sa.String(16), nullable=True),
        sa.Column("evidence_at", sa.Float, nullable=True),
        sa.Column("grounded", sa.Boolean, nullable=True),
        sa.Column("document_id", sa.Integer, *([sa.ForeignKey("documents.id", ondelete="SET NULL")] if postgres else []), nullable=True),
    ]
    for col in new_item_cols:
        if col.name not in have:
            op.add_column("items", col)
    if "ix_items_document_id" not in {i["name"] for i in insp.get_indexes("items")}:
        op.create_index("ix_items_document_id", "items", ["document_id"])
    have_jobs = _columns(insp, "jobs")
    if "kind" not in have_jobs:
        op.add_column("jobs", sa.Column("kind", sa.String(8), nullable=False, server_default="url"))
    if "payload" not in have_jobs:
        op.add_column("jobs", sa.Column("payload", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "payload")
    op.drop_column("jobs", "kind")
    op.drop_index("ix_items_document_id", table_name="items")
    for c in ("document_id", "grounded", "evidence_at", "evidence_source", "evidence", "venue_en"):
        op.drop_column("items", c)
    op.drop_table("documents")
