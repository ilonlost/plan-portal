"""Editable dispatch date and deadline per production card."""
from alembic import op
import sqlalchemy as sa

revision = "20261023_0023"
down_revision = "20261019_0022"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("production_schedule_items", sa.Column("shipping_date_override", sa.Date(), nullable=True))
    op.add_column("production_schedule_items", sa.Column("shipping_time_override", sa.Time(), nullable=True))


def downgrade():
    op.drop_column("production_schedule_items", "shipping_time_override")
    op.drop_column("production_schedule_items", "shipping_date_override")
