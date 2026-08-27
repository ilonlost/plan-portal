"""workshops, demand priority sources and execution reporting

Revision ID: 20260827_0003
Revises: 20260815_0002
"""
from alembic import op
import sqlalchemy as sa


revision = "20260827_0003"
down_revision = "20260815_0002"
branch_labels = None
depends_on = None


def _add(table: str, column: sa.Column) -> None:
    inspector = sa.inspect(op.get_bind())
    if column.name not in {item["name"] for item in inspector.get_columns(table)}:
        op.add_column(table, column)


def upgrade() -> None:
    for column in (
        sa.Column("legacy_quantum_units", sa.Numeric(14, 3)),
        sa.Column("legacy_daily_capacity_units", sa.Numeric(14, 3)),
        sa.Column("legacy_capacity_unit", sa.String(40)),
        sa.Column("recipe_component_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reference_source", sa.String(240)),
    ):
        _add("products", column)
    for column in (
        sa.Column("workshop_code", sa.String(20), nullable=False, server_default="UNASSIGNED"),
        sa.Column("workshop_name", sa.String(80), nullable=False, server_default="Не распределено"),
    ):
        _add("production_lines", column)
    for column in (
        sa.Column("source_kind", sa.String(30), nullable=False, server_default="generic"),
        sa.Column("source_plan_date", sa.Date()),
        sa.Column("marking_date", sa.Date()),
        sa.Column("advance_production", sa.Boolean(), nullable=False, server_default=sa.false()),
    ):
        _add("demand_items", column)
    for column in (
        sa.Column("source_kind", sa.String(30), nullable=False, server_default="generic"),
        sa.Column("marking_date", sa.Date()),
        sa.Column("execution_status", sa.String(30), nullable=False, server_default="not_started"),
        sa.Column("execution_note", sa.Text()),
        sa.Column("reported_by", sa.String(120)),
        sa.Column("reported_at", sa.DateTime(timezone=True)),
    ):
        _add("production_schedule_items", column)
    op.create_index("ix_production_lines_workshop_code", "production_lines", ["workshop_code"], unique=False, if_not_exists=True)
    op.create_index("ix_demand_items_source_kind", "demand_items", ["source_kind"], unique=False, if_not_exists=True)
    op.create_index("ix_production_schedule_items_source_kind", "production_schedule_items", ["source_kind"], unique=False, if_not_exists=True)


def downgrade() -> None:
    op.drop_index("ix_production_schedule_items_source_kind", table_name="production_schedule_items", if_exists=True)
    op.drop_index("ix_demand_items_source_kind", table_name="demand_items", if_exists=True)
    op.drop_index("ix_production_lines_workshop_code", table_name="production_lines", if_exists=True)
    for column in ("reported_at", "reported_by", "execution_note", "execution_status", "marking_date", "source_kind"):
        op.drop_column("production_schedule_items", column)
    for column in ("advance_production", "marking_date", "source_plan_date", "source_kind"):
        op.drop_column("demand_items", column)
    for column in ("workshop_name", "workshop_code"):
        op.drop_column("production_lines", column)
    for column in ("reference_source", "recipe_component_count", "legacy_capacity_unit", "legacy_daily_capacity_units", "legacy_quantum_units"):
        op.drop_column("products", column)
