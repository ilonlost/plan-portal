"""add feedback center tables

Revision ID: 20260901_0009
Revises: 20260831_0008
"""
from alembic import op
import sqlalchemy as sa


revision = "20260901_0009"
down_revision = "20260831_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("feedback_entries"):
        op.create_table(
            "feedback_entries",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("category", sa.String(30), nullable=False, server_default="suggestion"),
            sa.Column("subject", sa.String(180), nullable=False),
            sa.Column("message", sa.Text(), nullable=False),
            sa.Column("author_username", sa.String(120), nullable=False),
            sa.Column("author_name", sa.String(200), nullable=False),
            sa.Column("author_email", sa.String(200)),
            sa.Column("ip_address", sa.String(80)),
            sa.Column("user_agent", sa.String(500)),
            sa.Column("status", sa.String(30), nullable=False, server_default="new"),
            sa.Column("it_comment", sa.Text()),
            sa.Column("notification_status", sa.String(30), nullable=False, server_default="pending"),
            sa.Column("notification_error", sa.Text()),
            sa.Column("resolved_at", sa.DateTime(timezone=True)),
            sa.Column("resolved_by", sa.String(200)),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("feedback_entries")}
    if "ix_feedback_entries_author_username" not in indexes:
        op.create_index("ix_feedback_entries_author_username", "feedback_entries", ["author_username"])
    if "ix_feedback_entries_status" not in indexes:
        op.create_index("ix_feedback_entries_status", "feedback_entries", ["status"])

    if not inspector.has_table("feedback_events"):
        op.create_table(
            "feedback_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("feedback_id", sa.Integer(), sa.ForeignKey("feedback_entries.id", ondelete="CASCADE"), nullable=False),
            sa.Column("action", sa.String(40), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("actor_username", sa.String(120), nullable=False),
            sa.Column("actor_name", sa.String(200), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
    event_indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("feedback_events")}
    if "ix_feedback_events_feedback_id" not in event_indexes:
        op.create_index("ix_feedback_events_feedback_id", "feedback_events", ["feedback_id"])


def downgrade() -> None:
    op.drop_table("feedback_events")
    op.drop_table("feedback_entries")
