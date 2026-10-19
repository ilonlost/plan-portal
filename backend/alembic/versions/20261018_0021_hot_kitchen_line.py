"""Add a KC line for semi-finished products without changing existing lines."""
from datetime import date
from alembic import op
import sqlalchemy as sa

revision = "20261018_0021"
down_revision = "20261018_0020"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    lines = sa.Table("production_lines", sa.MetaData(), autoload_with=bind)
    rows = list(bind.execute(sa.select(lines.c.name, lines.c.workshop_code, lines.c.priority)).mappings())
    # On a fresh database seed creates the complete canonical structure.
    if not rows or any(row["name"].strip().casefold() == "горячий цех" and row["workshop_code"] == "KC" for row in rows):
        return
    bind.execute(lines.insert().values(
        code="FK-KC-HOT", name="Горячий цех", workshop_code="KC", workshop_name="КЦ",
        status="active", working_hours=22, default_capacity=0, capacity_unit="кг/день",
        priority=max(int(row["priority"] or 0) for row in rows) + 10,
        comments="Линия полуфабрикатов. Привязки артикулов и скорости задаются в справочнике.",
        schedule_code="day_daily", schedule_anchor_date=date(2026, 8, 28),
        custom_schedule_pattern=[], production_day_start_hour=0, csb_t5="4", planning_settings={},
    ))


def downgrade():
    # Preserve the production line and any tasks / capabilities subsequently attached to it.
    pass
