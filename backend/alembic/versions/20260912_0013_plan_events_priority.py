"""Add manual plan order and line planning settings.

Revision ID: 20260912_0013
Revises: 20260909_0012
"""
from alembic import op
import sqlalchemy as sa


revision = "20260912_0013"
down_revision = "20260909_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    line_columns = {column["name"] for column in inspector.get_columns("production_lines")}
    if "planning_settings" not in line_columns:
        op.add_column("production_lines", sa.Column("planning_settings", sa.JSON(), server_default="{}", nullable=False))
    item_columns = {column["name"] for column in inspector.get_columns("production_schedule_items")}
    if "sort_rank" not in item_columns:
        op.add_column("production_schedule_items", sa.Column("sort_rank", sa.Numeric(12, 3), nullable=True))


def downgrade() -> None:
    op.drop_column("production_schedule_items", "sort_rank")
    op.drop_column("production_lines", "planning_settings")
