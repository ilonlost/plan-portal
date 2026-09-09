"""Add non-destructive catalogue lifecycle state.

Revision ID: 20260909_0012
Revises: 20260908_0011
"""
from alembic import op
import sqlalchemy as sa


revision = "20260909_0012"
down_revision = "20260908_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "catalog_status" not in {column["name"] for column in inspector.get_columns("products")}:
        op.add_column("products", sa.Column("catalog_status", sa.String(20), server_default="active", nullable=False))
        op.create_index("ix_products_catalog_status", "products", ["catalog_status"])
    op.execute("UPDATE products SET catalog_status = CASE WHEN active = false THEN 'blocked' ELSE 'active' END")


def downgrade() -> None:
    op.drop_index("ix_products_catalog_status", table_name="products")
    op.drop_column("products", "catalog_status")
