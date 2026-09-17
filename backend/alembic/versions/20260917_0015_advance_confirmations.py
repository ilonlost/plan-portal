"""Add advance confirmation history.

Revision ID: 20260917_0015
Revises: 20260916_0014
"""
from alembic import op
import sqlalchemy as sa


revision = "20260917_0015"
down_revision = "20260916_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "advance_confirmation_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("file_name", sa.String(240), nullable=False),
        sa.Column("marking_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False, server_default="applied"),
        sa.Column("total_rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_quantity_kg", sa.Numeric(18, 3), nullable=False, server_default="0"),
        sa.Column("total_actual_kg", sa.Numeric(18, 3), nullable=False, server_default="0"),
        sa.Column("created_by", sa.String(120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_advance_confirmation_batches_marking_date", "advance_confirmation_batches", ["marking_date"])
    op.create_index("ix_advance_confirmation_batches_status", "advance_confirmation_batches", ["status"])
    op.create_table(
        "advance_confirmation_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("batch_id", sa.Integer(), sa.ForeignKey("advance_confirmation_batches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_row", sa.Integer(), nullable=False),
        sa.Column("product_id", sa.Integer(), sa.ForeignKey("products.id"), nullable=False),
        sa.Column("line_id", sa.Integer(), sa.ForeignKey("production_lines.id"), nullable=False),
        sa.Column("sku", sa.String(80), nullable=False),
        sa.Column("product_name", sa.String(200), nullable=False),
        sa.Column("line_name", sa.String(200), nullable=False),
        sa.Column("production_date", sa.Date(), nullable=False),
        sa.Column("marking_date", sa.Date(), nullable=False),
        sa.Column("previous_quantity_kg", sa.Numeric(18, 3), nullable=False, server_default="0"),
        sa.Column("quantity_kg", sa.Numeric(18, 3), nullable=False),
        sa.Column("actual_quantity_kg", sa.Numeric(18, 3), nullable=False, server_default="0"),
        sa.Column("delta_quantity_kg", sa.Numeric(18, 3), nullable=False, server_default="0"),
        sa.Column("advance_status", sa.String(40)),
        sa.Column("demand_item_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("schedule_item_ids", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("warnings", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    for column in ("batch_id", "product_id", "line_id", "sku", "production_date", "marking_date"):
        op.create_index(f"ix_advance_confirmation_items_{column}", "advance_confirmation_items", [column])


def downgrade() -> None:
    op.drop_table("advance_confirmation_items")
    op.drop_table("advance_confirmation_batches")
