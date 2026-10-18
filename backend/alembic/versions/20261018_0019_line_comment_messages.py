"""Append-only conversations for production lines; legacy comments are retained."""
from alembic import op
import sqlalchemy as sa

revision = "20261018_0019"
down_revision = "20260924_0018"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "line_comment_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("line_id", sa.Integer(), sa.ForeignKey("production_lines.id", ondelete="CASCADE"), nullable=False),
        sa.Column("comment_date", sa.Date(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("author_username", sa.String(120), nullable=False),
        sa.Column("author_name", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_line_comment_messages_line_id", "line_comment_messages", ["line_id"])
    op.create_index("ix_line_comment_messages_comment_date", "line_comment_messages", ["comment_date"])


def downgrade():
    op.drop_table("line_comment_messages")
