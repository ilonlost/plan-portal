"""synchronize canonical FK lines and CSB codes

Revision ID: 20260908_0011
Revises: 20260908_0010
"""
from datetime import date

from alembic import op
import sqlalchemy as sa


revision = "20260908_0011"
down_revision = "20260908_0010"
branch_labels = None
depends_on = None


LINES = (
    ("PC", "ПЦ", "Булка", "5810"),
    ("PC", "ПЦ", "Слойка", "5800"),
    ("PC", "ПЦ", "Хлеба", "5820"),
    ("PC", "ПЦ", "Ручная зона ПЦ", "5830"),
    ("PC", "ПЦ", "Сухари", "5850"),
    ("PC", "ПЦ", "НАЧИНКА", "5600"),
    ("KC", "КЦ", "Сэндвичи", "5480"),
    ("KC", "КЦ", "Жареные блюда", "5410"),
    ("KC", "КЦ", "Лазанья", "5440"),
    ("KC", "КЦ", "Миквак", "5400"),
    ("KC", "КЦ", "Бургеры", "5460"),
    ("KC", "КЦ", "Супы", "5430"),
    ("KC", "КЦ", "Салаты", "5420"),
    ("KC", "КЦ", "Напитки", "5470"),
    ("KC", "КЦ", "Ручная зона КЦ", "5410"),
)


def _key(value: str) -> str:
    return " ".join(value.lower().replace("ё", "е").replace("линия ", "").split())


def upgrade() -> None:
    bind = op.get_bind()
    rows = list(bind.execute(sa.text("SELECT id, code, name, priority FROM production_lines ORDER BY id")).mappings())
    # Fresh databases are populated by app.seed, which also creates the
    # starter planning rules and plan. Existing installations are normalized
    # in place by this migration.
    if not rows:
        return
    by_key: dict[str, list[dict]] = {}
    for row in rows:
        by_key.setdefault(_key(row["name"]), []).append(row)

    max_priority = max((int(row["priority"] or 0) for row in rows), default=0)
    existing_codes = {row["code"] for row in rows}
    for workshop_code, workshop_name, name, csb_code in LINES:
        key = _key(name)
        matches = by_key.get(key, [])
        if not matches:
            max_priority += 10
            portal_code = f"FK-{workshop_code}-{max_priority:03d}"
            while portal_code in existing_codes:
                max_priority += 10
                portal_code = f"FK-{workshop_code}-{max_priority:03d}"
            bind.execute(sa.text("""
                INSERT INTO production_lines
                    (code, name, workshop_code, workshop_name, status, working_hours,
                     default_capacity, capacity_unit, priority, comments, schedule_code,
                     schedule_anchor_date, custom_schedule_pattern, production_day_start_hour,
                     csb_line_code, csb_t5)
                VALUES
                    (:code, :name, :workshop_code, :workshop_name, 'active', 22,
                     0, 'кг/день', :priority, 'Каноническая структура линий ФК',
                     'day_daily', :anchor, '[]', :day_start, :csb_code, '4')
            """), {
                "code": portal_code, "name": name, "workshop_code": workshop_code,
                "workshop_name": workshop_name, "priority": max_priority,
                "anchor": date(2026, 8, 28), "day_start": 15 if workshop_code == "PC" else 0,
                "csb_code": csb_code,
            })
            inserted = bind.execute(sa.text("SELECT id, code, name, priority FROM production_lines WHERE code = :code"), {"code": portal_code}).mappings().one()
            matches = [inserted]
            by_key[key] = matches
            existing_codes.add(portal_code)

        keeper = matches[0]
        bind.execute(sa.text("""
            UPDATE production_lines
            SET name = :name, workshop_code = :workshop_code, workshop_name = :workshop_name,
                status = 'active', csb_line_code = :csb_code,
                production_day_start_hour = :day_start
            WHERE id = :line_id
        """), {
            "name": name, "workshop_code": workshop_code, "workshop_name": workshop_name,
            "csb_code": csb_code, "day_start": 15 if workshop_code == "PC" else 0,
            "line_id": keeper["id"],
        })
        for duplicate in matches[1:]:
            bind.execute(sa.text("UPDATE production_lines SET status = 'inactive' WHERE id = :line_id"), {"line_id": duplicate["id"]})

    official = {_key(name) for _, _, name, _ in LINES}
    for row in rows:
        if _key(row["name"]) not in official:
            bind.execute(sa.text("UPDATE production_lines SET status = 'inactive' WHERE id = :line_id"), {"line_id": row["id"]})


def downgrade() -> None:
    # Catalogue synchronization is data cleanup; reverting schema must not
    # re-enable duplicate or unrecognized production lines.
    pass
