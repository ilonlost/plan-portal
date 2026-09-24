"""Keep source workbook for AZ fact export."""
from alembic import op
import sqlalchemy as sa

revision = "20260924_0018"
down_revision = "20260920_0017"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("advance_confirmation_batches", sa.Column("source_workbook", sa.LargeBinary(), nullable=True))


def downgrade():
    op.drop_column("advance_confirmation_batches", "source_workbook")
