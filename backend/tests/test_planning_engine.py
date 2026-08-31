from datetime import date
from decimal import Decimal

from app.services.planning_engine import CapabilityInput, CapacityInput, DemandInput, PlanningEngine


def test_engine_splits_and_reports_overload() -> None:
    day = date(2026, 8, 17)
    items = PlanningEngine().plan(
        demands=[DemandInput(1, 10, "SKU", Decimal("1000"), day, day)],
        capabilities=[CapabilityInput(2, 10, Decimal("100"))],
        capacities=[CapacityInput(2, day, Decimal("8"))],
        horizon_end=day,
    )
    assert [item.quantity for item in items] == [Decimal("800"), Decimal("200")]
    assert [item.status for item in items] == ["planned", "conflict"]


def test_engine_keeps_incompatible_demand_visible() -> None:
    day = date(2026, 8, 17)
    items = PlanningEngine().plan(
        demands=[DemandInput(1, 10, "UNKNOWN", Decimal("10"), day, day)],
        capabilities=[], capacities=[], horizon_end=day,
    )
    assert items[0].status == "unscheduled"
    assert items[0].line_id is None


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
    assert {item.production_date for item in items} == {source_day}
    assert sum(item.quantity for item in items) == Decimal("3")
    assert items[-1].status == "conflict"
    assert items[-1].box_count == Decimal("1.000")


def test_ohl_kg_source_is_not_converted_or_rounded_to_box_quantum() -> None:
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
    assert items[0].quantity == Decimal("28.4")
    assert items[0].source_quantity == Decimal("28.4")
    assert items[0].source_unit == "кг"
    assert items[0].box_count == Decimal("47.333")


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
    assert sum(item.quantity for item in items if item.status != "conflict") == Decimal("800")
    assert sum(item.quantity for item in items if item.demand_id == 1 and item.status == "conflict") == Decimal("200")


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
    assert items[1].status == "conflict"
