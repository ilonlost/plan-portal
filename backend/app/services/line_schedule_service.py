from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import LineCapacity, ProductionLine


SCHEDULE_LABELS = {
    "day_daily": "Каждый день · дневная смена 11 ч",
    "two_shift_daily": "Каждый день · день и ночь по 11 ч",
    "two_two_day": "2/2 · только дневная смена 11 ч",
    "bread_cycle": "Хлеб · 2 дня по 22 ч, 2 дня по 11 ч",
    "custom": "Пользовательский шаблон",
}
DEFAULT_ANCHOR = date(2026, 8, 28)


def default_schedule_code(line_name: str) -> str:
    name = line_name.strip().lower()
    if name == "сэндвичи":
        return "two_shift_daily"
    if name in {"напитки", "сухари", "слойка"}:
        return "two_two_day"
    if name == "хлеба":
        return "bread_cycle"
    return "day_daily"


def shift_hours(line: ProductionLine, day: date) -> tuple[Decimal, Decimal]:
    code = line.schedule_code or default_schedule_code(line.name)
    anchor = line.schedule_anchor_date or DEFAULT_ANCHOR
    cycle_day = (day - anchor).days % 4
    if code == "custom" and line.custom_schedule_pattern:
        pattern = line.custom_schedule_pattern
        slot = pattern[(day - anchor).days % len(pattern)]
        return Decimal(str(slot.get("day_hours", 0))), Decimal(str(slot.get("night_hours", 0)))
    if code == "two_shift_daily":
        return Decimal("11"), Decimal("11")
    if code == "two_two_day":
        return (Decimal("11"), Decimal("0")) if cycle_day < 2 else (Decimal("0"), Decimal("0"))
    if code == "bread_cycle":
        return Decimal("11"), Decimal("11") if cycle_day < 2 else Decimal("0")
    return Decimal("11"), Decimal("0")


def ensure_line_capacities(
    db: Session,
    lines: list[ProductionLine],
    start: date,
    end: date,
    refresh_generated: bool = False,
) -> None:
    line_ids = [line.id for line in lines]
    existing = {(row.line_id, row.capacity_date, row.shift): row for row in db.scalars(select(LineCapacity).where(
        LineCapacity.line_id.in_(line_ids), LineCapacity.capacity_date >= start, LineCapacity.capacity_date <= end,
    ))} if line_ids else {}
    day = start
    while day <= end:
        for line in lines:
            day_hours, night_hours = shift_hours(line, day)
            for shift, hours in (("day", day_hours), ("night", night_hours)):
                row = existing.get((line.id, day, shift))
                if row and (row.manual_override or not refresh_generated):
                    continue
                if not row:
                    row = LineCapacity(line_id=line.id, capacity_date=day, shift=shift)
                    db.add(row)
                row.available_hours = hours
                row.available = hours > 0
                row.manual_override = False
                row.note = SCHEDULE_LABELS.get(line.schedule_code or "day_daily", "График линии")
        day += timedelta(days=1)
    db.flush()
