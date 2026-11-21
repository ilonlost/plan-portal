"""Store daily headcounts entered by line masters."""
from alembic import op
import sqlalchemy as sa

revision = "20261119_0025"
down_revision = "20261108_0024"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "line_actual_attendance",
        sa.Column("line_id", sa.Integer(), sa.ForeignKey("production_lines.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("attendance_date", sa.Date(), primary_key=True),
        sa.Column("headcount", sa.Integer(), sa.CheckConstraint("headcount >= 0", name="ck_line_actual_attendance_headcount"), nullable=False),
        sa.Column("author_username", sa.String(120), nullable=False),
        sa.Column("author_name", sa.String(200), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_line_actual_attendance_attendance_date", "line_actual_attendance", ["attendance_date"])


def downgrade():
    op.drop_table("line_actual_attendance")
