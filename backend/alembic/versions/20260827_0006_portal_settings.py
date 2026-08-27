"""add editable portal settings

Revision ID: 20260827_0006
Revises: 20260827_0005
"""
from alembic import op
import sqlalchemy as sa


revision = "20260827_0006"
down_revision = "20260827_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "portal_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("updated_by", sa.String(120)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("key", name="uq_portal_settings_key"),
    )
    op.create_index("ix_portal_settings_key", "portal_settings", ["key"], unique=True)


def downgrade() -> None:
    op.drop_table("portal_settings")
