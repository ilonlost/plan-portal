"""Store the order number confirmed by CSB."""
from alembic import op
import sqlalchemy as sa

revision = "20261119_0027"
down_revision = "20261119_0026"
branch_labels = None
depends_on = None


def upgrade():
    table = "production_schedule_items"
    if "csb_order_number" not in {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}:
        op.add_column(table, sa.Column("csb_order_number", sa.Text()))


def downgrade():
    op.drop_column("production_schedule_items", "csb_order_number")
