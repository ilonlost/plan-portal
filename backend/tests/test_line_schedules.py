from datetime import date
from decimal import Decimal

from app.models.entities import ProductionLine
from app.services.line_schedule_service import default_schedule_code, shift_hours
from app.services.planning_rules import mono_group


ANCHOR = date(2026, 8, 28)


def line(name: str, code: str) -> ProductionLine:
    return ProductionLine(
        code=f"TEST-{name}", name=name, workshop_code="PC", workshop_name="ПЦ",
        schedule_code=code, schedule_anchor_date=ANCHOR,
    )


def test_seed_schedule_mapping_matches_production_rules() -> None:
    assert default_schedule_code("Сэндвичи") == "two_shift_daily"
    assert default_schedule_code("Напитки") == "two_two_day"
    assert default_schedule_code("Сухари") == "two_two_day"
    assert default_schedule_code("Слойка") == "two_two_day"
    assert default_schedule_code("Хлеба") == "bread_cycle"
    assert default_schedule_code("Ручная зона 1") == "day_daily"


def test_shift_hours_for_all_requested_patterns() -> None:
    assert shift_hours(line("Сэндвичи", "two_shift_daily"), ANCHOR) == (Decimal("11"), Decimal("11"))
    two_two = line("Напитки", "two_two_day")
    assert shift_hours(two_two, ANCHOR) == (Decimal("11"), Decimal("0"))
    assert shift_hours(two_two, date(2026, 8, 30)) == (Decimal("0"), Decimal("0"))
    bread = line("Хлеба", "bread_cycle")
    assert shift_hours(bread, ANCHOR) == (Decimal("11"), Decimal("11"))
    assert shift_hours(bread, date(2026, 8, 30)) == (Decimal("11"), Decimal("0"))
    assert shift_hours(line("Ручная зона", "day_daily"), ANCHOR) == (Decimal("11"), Decimal("0"))


def test_burger_buns_form_one_mono_product_group() -> None:
    names = [
        "Бул. для бург. 165*6(0,99кг)FP 60сут",
        "Бул. для бург. 240г*6(1.44кг)FP 90сут",
    ]
    assert len({mono_group(name) for name in names}) == 1
    assert mono_group("Булочка бриошь 210г*6(1,26кг)FP 90сут") != mono_group(names[0])
