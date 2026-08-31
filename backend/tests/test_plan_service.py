from decimal import Decimal

from app.models.entities import Product, ProductionScheduleItem, ScheduleStatus
from app.services.plan_service import schedule_item_dict


def test_schedule_item_rounds_derived_units_up_to_whole_piece() -> None:
    product = Product(sku="SKU-1", name="Тестовая продукция", unit_weight_kg=Decimal("0.16"))
    item = ProductionScheduleItem(
        product=product, quantity=Decimal("287.92"), quantity_kg=Decimal("287.92"), source_unit="кг", status=ScheduleStatus.PLANNED,
    )

    assert schedule_item_dict(item)["quantity_units"] == Decimal("1800")


def test_schedule_item_uses_whole_pieces_for_a_full_box() -> None:
    product = Product(
        sku="SKU-BOX", name="Тестовая продукция", unit_weight_kg=Decimal("0.15"),
        units_per_box=Decimal("4"), box_weight_kg=Decimal("0.6"),
    )
    item = ProductionScheduleItem(
        product=product, quantity=Decimal("29.4"), quantity_kg=Decimal("29.4"),
        box_count=Decimal("49"), source_unit="кг", status=ScheduleStatus.PLANNED,
    )

    data = schedule_item_dict(item)
    assert data["quantity_units"] == Decimal("196")
    assert data["box_count"] == Decimal("49")
