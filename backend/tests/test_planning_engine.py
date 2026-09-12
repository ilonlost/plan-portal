from datetime import date
from decimal import Decimal

from app.services.planning_engine import CapabilityInput, CapacityInput, DemandInput, PlanningEngine
from app.services.planning_rules import mono_group


def test_lasagna_ohl_and_zam_share_the_same_variant_group() -> None:
    ohl = 'Лазанья "Болоньезе" с сыром в соусе "Бешамель" охл 350г*4'
    zam = 'Лазанья "Болоньезе" с сыром в соусе "Бешамель" зам 350г*6'
    assert mono_group(ohl) == mono_group(zam) == "Лазанья Болоньезе"


def test_engine_splits_and_reports_overload() -> None:
    day = date(2026, 8, 17)
    items = PlanningEngine().plan(
        demands=[DemandInput(1, 10, "SKU", Decimal("1000"), day, day)],
        capabilities=[CapabilityInput(2, 10, Decimal("100"))],
        capacities=[CapacityInput(2, day, Decimal("8"))],
        horizon_end=day,
    )
    assert [item.quantity for item in items] == [Decimal("800"), Decimal("200")]
    assert [item.status for item in items] == ["planned", "unscheduled"]
    assert items[1].production_date is None


def test_engine_keeps_incompatible_demand_visible() -> None:
    day = date(2026, 8, 17)
    items = PlanningEngine().plan(
        demands=[DemandInput(1, 10, "UNKNOWN", Decimal("10"), day, day)],
        capabilities=[], capacities=[], horizon_end=day,
    )
    assert items[0].status == "unscheduled"
    assert items[0].line_id is None


def test_incompatible_demand_still_rounds_to_full_boxes() -> None:
    day = date(2026, 8, 17)
    items = PlanningEngine().plan(
        demands=[DemandInput(
            1, 10, "PACKED", Decimal("28.4"), day, day, source_unit="кг",
            unit_weight_kg=Decimal("0.15"), units_per_box=Decimal("4"), box_quantum_kg=Decimal("0.6"),
        )],
        capabilities=[], capacities=[], horizon_end=day,
    )

    assert items[0].status == "unscheduled"
    assert items[0].quantity == Decimal("28.8")
    assert items[0].box_count == Decimal("48.000")


def test_product_without_box_is_rounded_to_whole_pieces() -> None:
    day = date(2026, 8, 17)
    items = PlanningEngine().plan(
        demands=[DemandInput(
            1, 10, "PIECE", Decimal("1.01"), day, day, source_unit="кг", unit_weight_kg=Decimal("0.15"),
        )],
        capabilities=[CapabilityInput(2, 10, Decimal("100"))],
        capacities=[CapacityInput(2, day, Decimal("1"))], horizon_end=day,
    )

    assert items[0].quantity == Decimal("1.05")
    assert items[0].source_quantity == Decimal("1.05")


def test_ohl_demand_stays_on_source_date_and_uses_full_boxes() -> None:
    source_day = date(2026, 8, 12)
    next_day = date(2026, 8, 13)
    items = PlanningEngine().plan(
        demands=[DemandInput(
            1, 10, "OHL", Decimal("2.5"), source_day, source_day,
            source_quantity=Decimal("10"), source_unit="шт", unit_weight_kg=Decimal("0.25"),
            units_per_box=Decimal("4"), box_quantum_kg=Decimal("1"), exact_date=True,
        )],
        capabilities=[CapabilityInput(2, 10, Decimal("1"))],
        capacities=[
            CapacityInput(2, source_day, Decimal("2"), shift="day"),
            CapacityInput(2, next_day, Decimal("12"), shift="day"),
        ],
        horizon_end=next_day,
    )
    assert {item.production_date for item in items} == {source_day, None}
    assert sum(item.quantity for item in items) == Decimal("3")
    assert items[-1].status == "unscheduled"
    assert items[-1].box_count == Decimal("1.000")


def test_ohl_kg_source_rounds_up_to_whole_boxes_and_pieces() -> None:
    day = date(2026, 8, 12)
    items = PlanningEngine().plan(
        demands=[DemandInput(
            1, 10, "OHL-KG", Decimal("28.4"), day, day,
            source_quantity=Decimal("28.4"), source_unit="кг", unit_weight_kg=Decimal("0.15"),
            units_per_box=Decimal("4"), box_quantum_kg=Decimal("0.6"), exact_date=True, source_kind="ohl",
        )],
        capabilities=[CapabilityInput(2, 10, Decimal("100"), batch_quantum_kg=Decimal("1"))],
        capacities=[CapacityInput(2, day, Decimal("1"), shift="day")],
        horizon_end=day,
    )
    assert len(items) == 1
    assert items[0].quantity == Decimal("28.8")
    assert items[0].source_quantity == Decimal("28.8")
    assert items[0].source_unit == "кг"
    assert items[0].box_count == Decimal("48.000")


def test_weekly_plan_balances_day_and_night_shifts_by_batch() -> None:
    monday = date(2026, 8, 10)
    items = PlanningEngine().plan(
        demands=[DemandInput(1, 10, "ZAM", Decimal("400"), monday, monday)],
        capabilities=[CapabilityInput(2, 10, Decimal("100"), batch_quantum_kg=Decimal("100"))],
        capacities=[
            CapacityInput(2, monday, Decimal("2"), shift="day"),
            CapacityInput(2, monday, Decimal("2"), shift="night"),
        ],
        horizon_end=monday,
    )
    assert {item.shift for item in items} == {"day", "night"}
    assert all(item.quantity % Decimal("100") == 0 for item in items)


def test_zam_remainder_uses_free_capacity_after_source_week() -> None:
    monday = date(2026, 8, 31)
    sunday = date(2026, 9, 6)
    next_monday = date(2026, 9, 7)
    items = PlanningEngine().plan(
        demands=[DemandInput(
            1, 10, "ZAM", Decimal("600"), monday, sunday,
            source_kind="zam", exact_date=False,
        )],
        capabilities=[CapabilityInput(2, 10, Decimal("100"), batch_quantum_kg=Decimal("100"))],
        capacities=[
            CapacityInput(2, monday, Decimal("2"), shift="day"),
            CapacityInput(2, next_monday, Decimal("4"), shift="day"),
        ],
        horizon_end=next_monday,
    )

    assert [(item.production_date, item.quantity) for item in items] == [
        (monday, Decimal("200")),
        (next_monday, Decimal("400")),
    ]
    assert not any(item.status == "unscheduled" for item in items)


def test_zam_fills_earlier_daily_gap_before_later_empty_day() -> None:
    first = date(2026, 9, 7)
    second = date(2026, 9, 8)
    items = PlanningEngine().plan(
        demands=[
            DemandInput(1, 10, "OHL", Decimal("20"), first, first, source_kind="ohl", exact_date=True),
            DemandInput(2, 10, "ZAM", Decimal("200"), first, first, source_kind="zam", exact_date=False),
        ],
        capabilities=[CapabilityInput(2, 10, Decimal("100"), batch_quantum_kg=Decimal("20"))],
        capacities=[
            CapacityInput(2, first, Decimal("1"), shift="day"),
            CapacityInput(2, second, Decimal("2"), shift="day"),
        ],
        horizon_end=second,
    )

    zam = [item for item in items if item.source_kind == "zam"]
    assert [(item.production_date, item.quantity) for item in zam] == [
        (first, Decimal("80")),
        (second, Decimal("120")),
    ]


def test_ohl_uses_capacity_before_zam() -> None:
    day = date(2026, 8, 12)
    items = PlanningEngine().plan(
        demands=[
            DemandInput(1, 10, "ZAM", Decimal("600"), day, day, source_kind="zam"),
            DemandInput(2, 10, "OHL", Decimal("400"), day, day, exact_date=True, source_kind="ohl"),
        ],
        capabilities=[CapabilityInput(2, 10, Decimal("100"), batch_quantum_kg=Decimal("100"))],
        capacities=[CapacityInput(2, day, Decimal("8"))], horizon_end=day,
    )
    assert items[0].demand_id == 2
    assert items[0].source_kind == "ohl"
    assert sum(item.quantity for item in items if item.production_date is not None) == Decimal("800")
    assert sum(item.quantity for item in items if item.demand_id == 1 and item.status == "unscheduled") == Decimal("200")


def test_pc_mono_group_reserves_one_wash_per_group() -> None:
    day = date(2026, 8, 28)
    items = PlanningEngine().plan(
        demands=[
            DemandInput(1, 10, "BUN-165", Decimal("100"), day, day, source_kind="ohl", mono_group="Булочка для бургера"),
            DemandInput(2, 11, "BUN-240", Decimal("100"), day, day, source_kind="ohl", mono_group="Булочка для бургера"),
            DemandInput(3, 12, "HOTDOG", Decimal("100"), day, day, source_kind="ohl", mono_group="Булочка хот-дог"),
        ],
        capabilities=[
            CapabilityInput(2, 10, Decimal("100"), workshop_code="PC"),
            CapabilityInput(2, 11, Decimal("100"), workshop_code="PC"),
            CapabilityInput(2, 12, Decimal("100"), workshop_code="PC"),
        ],
        capacities=[CapacityInput(2, day, Decimal("5"))], horizon_end=day,
    )
    assert [item.status for item in items] == ["planned", "planned", "planned"]
    assert sum(item.required_hours for item in items) == Decimal("3.00")


def test_pc_washing_reduces_available_production_capacity() -> None:
    day = date(2026, 8, 28)
    items = PlanningEngine().plan(
        demands=[
            DemandInput(1, 10, "A", Decimal("100"), day, day, mono_group="A"),
            DemandInput(2, 11, "B", Decimal("100"), day, day, mono_group="B"),
        ],
        capabilities=[
            CapabilityInput(2, 10, Decimal("100"), workshop_code="PC"),
            CapabilityInput(2, 11, Decimal("100"), workshop_code="PC"),
        ],
        capacities=[CapacityInput(2, day, Decimal("3"))], horizon_end=day,
    )
    assert items[0].status == "planned"
    assert items[1].status == "unscheduled"


def test_line_startup_and_changeover_reduce_capacity() -> None:
    day = date(2026, 9, 14)
    items = PlanningEngine().plan(
        demands=[
            DemandInput(1, 10, "LAS-A", Decimal("100"), day, day, mono_group="A"),
            DemandInput(2, 11, "LAS-B", Decimal("100"), day, day, mono_group="B"),
        ],
        capabilities=[
            CapabilityInput(2, 10, Decimal("100"), daily_startup_hours=Decimal("1"), changeover_hours=Decimal("1")),
            CapabilityInput(2, 11, Decimal("100"), daily_startup_hours=Decimal("1"), changeover_hours=Decimal("1")),
        ],
        capacities=[CapacityInput(2, day, Decimal("3"))], horizon_end=day,
    )
    assert items[0].status == "planned"
    assert items[1].status == "unscheduled"
