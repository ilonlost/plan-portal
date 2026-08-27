"""set production lines to 22 working hours per day

Revision ID: 20260827_0005
Revises: 20260827_0004
"""
from alembic import op


revision = "20260827_0005"
down_revision = "20260827_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Two 12-hour calendar shifts include one meal hour each. Production time is
    # therefore capped at 11 hours per shift and 22 hours per line/day.
    op.execute("UPDATE production_lines SET default_capacity = default_capacity / 24 * 22 WHERE working_hours = 24")
    op.execute("UPDATE production_lines SET working_hours = 22 WHERE working_hours = 24")
    op.execute("UPDATE line_capacities SET available_hours = 11 WHERE available_hours = 12")
    op.execute("UPDATE line_capacities SET note = '11 ч производства + 1 ч обед' WHERE note = 'Автоматически создано: дневная/ночная смена'")


def downgrade() -> None:
    op.execute("UPDATE production_lines SET default_capacity = default_capacity / 22 * 24 WHERE working_hours = 22")
    op.execute("UPDATE production_lines SET working_hours = 24 WHERE working_hours = 22")
    op.execute("UPDATE line_capacities SET available_hours = 12 WHERE available_hours = 11")
    op.execute("UPDATE line_capacities SET note = 'Автоматически создано: дневная/ночная смена' WHERE note = '11 ч производства + 1 ч обед'")
