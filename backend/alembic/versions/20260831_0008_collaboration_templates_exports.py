"""add line templates, collaboration fields and export settings

Revision ID: 20260831_0008
Revises: 20260828_0007
"""
from alembic import op
import sqlalchemy as sa


revision = "20260831_0008"
down_revision = "20260828_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table("line_schedule_templates"):
        op.create_table(
            "line_schedule_templates",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(160), nullable=False, unique=True),
            sa.Column("description", sa.String(500)),
            sa.Column("pattern", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("created_by", sa.String(120)),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        )

    def add_if_missing(column: sa.Column) -> None:
        if column.name not in {value["name"] for value in sa.inspect(op.get_bind()).get_columns("production_lines")}:
            op.add_column("production_lines", column)

    add_if_missing(sa.Column("schedule_template_id", sa.Integer(), sa.ForeignKey("line_schedule_templates.id", ondelete="SET NULL")))
    add_if_missing(sa.Column("custom_schedule_pattern", sa.JSON(), nullable=False, server_default="[]"))
    add_if_missing(sa.Column("production_day_start_hour", sa.Integer(), nullable=False, server_default="0"))
    add_if_missing(sa.Column("mail_recipients", sa.Text()))
    add_if_missing(sa.Column("csb_line_code", sa.String(40)))
    add_if_missing(sa.Column("csb_t5", sa.String(20), nullable=False, server_default="4"))
    add_if_missing(sa.Column("csb_t55", sa.String(80)))
    op.execute("UPDATE production_lines SET production_day_start_hour = 15 WHERE workshop_code = 'PC'")
    op.execute("UPDATE production_lines SET csb_line_code = '5810' WHERE lower(name) = 'булка'")
    op.execute("UPDATE production_lines SET csb_line_code = '5720' WHERE lower(name) = 'слойка'")
    op.execute("UPDATE production_lines SET csb_line_code = '5820' WHERE lower(name) = 'хлеба'")
    op.execute("UPDATE production_lines SET csb_line_code = '5410' WHERE lower(name) = 'бургеры'")
    op.execute("UPDATE production_lines SET csb_line_code = '5440' WHERE lower(name) = 'жареные блюда'")
    op.execute("UPDATE production_lines SET csb_line_code = '5400' WHERE lower(name) = 'салаты'")
    op.execute("UPDATE production_lines SET csb_line_code = '5480' WHERE lower(name) = 'лазанья'")
    op.execute("UPDATE production_lines SET csb_line_code = '5460' WHERE lower(name) = 'сэндвичи'")
    op.execute("UPDATE production_schedule_items SET required_hours = 0.02, duration_hours = 0.02 WHERE schedule_kind = 'production' AND required_hours > 0 AND required_hours < 0.02")


def downgrade() -> None:
    for column in ("csb_t55", "csb_t5", "csb_line_code", "mail_recipients", "production_day_start_hour", "custom_schedule_pattern", "schedule_template_id"):
        op.drop_column("production_lines", column)
    op.drop_table("line_schedule_templates")
