"""Keep a durable outbox for CSB SDK submissions."""
from alembic import op
import sqlalchemy as sa

revision = "20261119_0028"
down_revision = "20261119_0027"
branch_labels = None
depends_on = None


def upgrade():
    if not sa.inspect(op.get_bind()).has_table("csb_submissions"):
        op.create_table(
            "csb_submissions",
            sa.Column("t63", sa.Integer(), primary_key=True, autoincrement=False),
            sa.Column("payload", sa.JSON(), nullable=False),
            sa.Column("state", sa.String(20), nullable=False),
            sa.Column("requested_by", sa.String(120), nullable=False),
            sa.Column("claim", sa.String(36)),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("order_number", sa.Text()),
            sa.Column("detail", sa.Text()),
        )


def downgrade():
    op.drop_table("csb_submissions")
