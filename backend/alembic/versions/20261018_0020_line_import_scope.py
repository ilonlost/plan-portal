"""Keep per-line uploads separate from the complete source workbook."""
from alembic import op
import sqlalchemy as sa

revision = "20261018_0020"
down_revision = "20261018_0019"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("imported_orders", sa.Column("scope_line_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_imported_orders_scope_line", "imported_orders", "production_lines", ["scope_line_id"], ["id"])
    op.create_index("ix_imported_orders_scope_line_id", "imported_orders", ["scope_line_id"])


def downgrade():
    op.drop_index("ix_imported_orders_scope_line_id", table_name="imported_orders")
    op.drop_constraint("fk_imported_orders_scope_line", "imported_orders", type_="foreignkey")
    op.drop_column("imported_orders", "scope_line_id")
