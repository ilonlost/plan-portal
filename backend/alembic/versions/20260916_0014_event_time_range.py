"""Add exact start and end time for technical events.

Revision ID: 20260916_0014
Revises: 20260912_0013
"""
from alembic import op
import sqlalchemy as sa


revision = "20260916_0014"
down_revision = "20260912_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("production_schedule_items")}
    if "start_time" not in columns:
        op.add_column("production_schedule_items", sa.Column("start_time", sa.Time(), nullable=True))
    if "end_time" not in columns:
        op.add_column("production_schedule_items", sa.Column("end_time", sa.Time(), nullable=True))


def downgrade() -> None:
    op.drop_column("production_schedule_items", "end_time")
    op.drop_column("production_schedule_items", "start_time")
