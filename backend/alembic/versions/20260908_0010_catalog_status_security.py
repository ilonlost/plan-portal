"""Catalogue status and retirement of seeded demo administrators."""
from alembic import op
import sqlalchemy as sa

revision = "20260908_0010"
down_revision = "20260901_0009"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    for table in ("demand_items", "production_schedule_items"):
        with op.batch_alter_table(table) as batch:
            for name in ("quantity", "source_quantity", "quantity_kg"):
                batch.alter_column(name, type_=sa.Numeric(18, 6), existing_type=sa.Numeric(12, 2) if name == "quantity" else sa.Numeric(14, 3))
    if "revision" not in {column["name"] for column in inspector.get_columns("production_plans")}:
        op.add_column("production_plans", sa.Column("revision", sa.Integer(), server_default="1", nullable=False))
    for name, length in (("advance_status", 40), ("fk_status", 120)):
        if name not in {column["name"] for column in inspector.get_columns("products")}:
            op.add_column("products", sa.Column(name, sa.String(length), nullable=True))
    # Retain identities and audit history; never delete ordinary users.
    op.execute("UPDATE users SET active = false WHERE lower(username) IN ('demo.admin', 'demo.planner')")


def downgrade():
    op.drop_column("production_plans", "revision")
    op.drop_column("products", "fk_status")
    op.drop_column("products", "advance_status")
    # Deliberately do not reactivate retired accounts on rollback.
