"""Add dated comments for production lines.

Revision ID: 20260920_0016
Revises: 20260917_0015
"""
from alembic import op
import sqlalchemy as sa

revision = "20260920_0016"
down_revision = "20260917_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "line_comments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("line_id", sa.Integer(), sa.ForeignKey("production_lines.id", ondelete="CASCADE"), nullable=False),
        sa.Column("comment_date", sa.Date(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("author_username", sa.String(120), nullable=False),
        sa.Column("author_name", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("line_id", "comment_date", name="uq_line_comments_line_date"),
    )
    op.create_index("ix_line_comments_line_id", "line_comments", ["line_id"])
    op.create_index("ix_line_comments_comment_date", "line_comments", ["comment_date"])


def downgrade() -> None:
    op.drop_table("line_comments")
