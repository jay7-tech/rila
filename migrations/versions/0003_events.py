"""Events: what users do with items (like, dismiss, visit, open). Feeds personalised ranking.

Revision ID: 0003
Revises: 0002

Migration 0001 builds tables from the current models, so on a brand-new database the table already exists.
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "events" in insp.get_table_names():
        return
    op.create_table(
        "events",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("item_id", sa.Integer, sa.ForeignKey("items.id", ondelete="SET NULL"), nullable=True, index=True),
        sa.Column("action", sa.String(12), nullable=False),
        sa.Column("category", sa.String(24), nullable=True),
        sa.Column("creator", sa.String(120), nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, index=True),
    )


def downgrade() -> None:
    op.drop_table("events")
