"""real source units, production quanta and manual events

Revision ID: 20260815_0002
Revises: 20260815_0001
"""
from alembic import op
import sqlalchemy as sa


revision = "20260815_0002"
down_revision = "20260815_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    columns = {table: {item["name"]: item for item in inspector.get_columns(table)} for table in (
        "products", "line_capabilities", "imported_orders", "demand_items", "production_schedule_items",
    )}
    additions = {
        "products": [
            sa.Column("unit_weight_kg", sa.Numeric(12, 6)), sa.Column("units_per_box", sa.Numeric(12, 3)),
            sa.Column("box_weight_kg", sa.Numeric(12, 4)), sa.Column("state", sa.String(40)),
            sa.Column("category", sa.String(120)), sa.Column("short_name", sa.String(200)),
        ],
        "line_capabilities": [
            sa.Column("speed_unit", sa.String(30), nullable=False, server_default="кг/час"),
            sa.Column("batch_quantum_kg", sa.Numeric(14, 4)), sa.Column("min_order_kg", sa.Numeric(14, 4)),
            sa.Column("capacity_type", sa.String(80)), sa.Column("restrictions", sa.Text()),
        ],
        "imported_orders": [sa.Column("template_type", sa.String(50), nullable=False, server_default="generic")],
        "demand_items": [
            sa.Column("source_quantity", sa.Numeric(14, 3)), sa.Column("source_unit", sa.String(30), nullable=False, server_default="кг"),
            sa.Column("quantity_kg", sa.Numeric(14, 3)), sa.Column("box_count", sa.Numeric(14, 3)),
            sa.Column("production_week", sa.Integer()), sa.Column("exact_date", sa.Boolean(), nullable=False, server_default=sa.false()),
        ],
        "production_schedule_items": [
            sa.Column("source_quantity", sa.Numeric(14, 3)), sa.Column("source_unit", sa.String(30), nullable=False, server_default="кг"),
            sa.Column("quantity_kg", sa.Numeric(14, 3)), sa.Column("box_count", sa.Numeric(14, 3)),
            sa.Column("batch_count", sa.Numeric(14, 3)), sa.Column("schedule_kind", sa.String(30), nullable=False, server_default="production"),
            sa.Column("duration_hours", sa.Numeric(7, 2)), sa.Column("reason", sa.Text()), sa.Column("actual_quantity_kg", sa.Numeric(14, 3)),
        ],
    }
    for table, table_columns in additions.items():
        for column in table_columns:
            if column.name not in columns[table]:
                op.add_column(table, column)
    if not columns["production_schedule_items"]["product_id"].get("nullable", True):
        op.alter_column("production_schedule_items", "product_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    for column in ("actual_quantity_kg", "reason", "duration_hours", "schedule_kind", "batch_count", "box_count", "quantity_kg", "source_unit", "source_quantity"):
        op.drop_column("production_schedule_items", column)
    op.alter_column("production_schedule_items", "product_id", existing_type=sa.Integer(), nullable=False)
    for column in ("exact_date", "production_week", "box_count", "quantity_kg", "source_unit", "source_quantity"):
        op.drop_column("demand_items", column)
    op.drop_column("imported_orders", "template_type")
    for column in ("restrictions", "capacity_type", "min_order_kg", "batch_quantum_kg", "speed_unit"):
        op.drop_column("line_capabilities", column)
    for column in ("short_name", "category", "state", "box_weight_kg", "units_per_box", "unit_weight_kg"):
        op.drop_column("products", column)
