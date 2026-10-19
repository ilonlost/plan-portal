"""Date-scoped imports and read-only service identities alongside AD."""
from alembic import op
import sqlalchemy as sa

revision = "20261019_0022"
down_revision = "20261018_0021"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("auth_provider", sa.String(20), server_default="ad", nullable=False))
    op.add_column("users", sa.Column("password_hash", sa.String(300), nullable=True))
    op.add_column("users", sa.Column("session_version", sa.Integer(), server_default="0", nullable=False))
    op.add_column("imported_orders", sa.Column("scope_start", sa.Date(), nullable=True))
    op.add_column("imported_orders", sa.Column("scope_end", sa.Date(), nullable=True))


def downgrade():
    for column in ("scope_end", "scope_start"):
        op.drop_column("imported_orders", column)
    for column in ("session_version", "password_hash", "auth_provider"):
        op.drop_column("users", column)
