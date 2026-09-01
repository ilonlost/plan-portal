"""add line schedules and mono-product groups

Revision ID: 20260828_0007
Revises: 20260827_0006
"""
from alembic import op
import sqlalchemy as sa


revision = "20260828_0007"
down_revision = "20260827_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    def add_if_missing(table: str, column: sa.Column) -> None:
        if column.name not in {value["name"] for value in sa.inspect(op.get_bind()).get_columns(table)}:
            op.add_column(table, column)

    add_if_missing("products", sa.Column("mono_group", sa.String(160)))
    if not any(value["name"] == "ix_products_mono_group" for value in sa.inspect(op.get_bind()).get_indexes("products")):
        op.create_index("ix_products_mono_group", "products", ["mono_group"])
    add_if_missing("production_lines", sa.Column("schedule_code", sa.String(40), nullable=False, server_default="day_daily"))
    add_if_missing("production_lines", sa.Column("schedule_anchor_date", sa.Date()))
    add_if_missing("line_capacities", sa.Column("manual_override", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.execute("UPDATE production_lines SET schedule_anchor_date = DATE '2026-08-28'")
    op.execute("UPDATE production_lines SET schedule_code = 'two_shift_daily' WHERE lower(name) = 'сэндвичи'")
    op.execute("UPDATE production_lines SET schedule_code = 'two_two_day' WHERE lower(name) IN ('напитки', 'сухари', 'слойка')")
    op.execute("UPDATE production_lines SET schedule_code = 'bread_cycle' WHERE lower(name) = 'хлеба'")
    op.execute("UPDATE production_lines SET schedule_code = 'day_daily' WHERE lower(name) LIKE 'ручная зона%'")


def downgrade() -> None:
    op.drop_column("line_capacities", "manual_override")
    op.drop_column("production_lines", "schedule_anchor_date")
    op.drop_column("production_lines", "schedule_code")
    op.drop_index("ix_products_mono_group", table_name="products")
    op.drop_column("products", "mono_group")
