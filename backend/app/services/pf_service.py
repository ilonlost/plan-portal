"""Explicit GP → PF expansion into the existing line capacity calendar."""
from datetime import date, timedelta
from decimal import Decimal
from functools import lru_cache
import json
import hashlib
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import LineCapability, Product, ProductionLine, ProductionScheduleItem, ScheduleStatus
from app.services.task_date_service import ohl_production_date, uses_sandwich_window
from app.services.bom_volume_service import calculate_components


@lru_cache(maxsize=1)
def source_data() -> dict:
    return json.loads((Path(__file__).parents[1] / "data" / "pf_rules.json").read_text(encoding="utf-8"))


def rules_for(sku: str) -> list[dict]:
    result = {}
    for row in source_data()["rows"]:
        if row["gp_sku"] != sku:
            continue
        for kind in ("pf", "filling"):
            component = row[f"{kind}_sku"]
            if not component:
                continue
            note = (row["note"] or "").strip()
            days = 7 if "7 дней" in note else 3 if "3ое" in note else 1 if note == "за сутки" else 0 if note.lower() == "день в день" else 2
            if "начинка за три дня" in note:
                days = 5 if kind == "filling" else 2
            key = component
            # The cinnamon roll occurs twice; its later row supplies the date rule.
            if key in result and not note:
                continue
            result[key] = {**row, "sku": component, "name": row[f"{kind}_name"],
                           "kind": kind, "lead_days": days,
                           "date_defaulted": not note or ("начинка за три дня" in note and kind == "pf")}
    return list(result.values())


def _target_line(rule: dict, lines: list[ProductionLine]) -> ProductionLine | None:
    if "line_id" in rule:
        return next((line for line in lines if line.id == rule["line_id"]), None)
    if rule["kind"] == "pf":
        target = {"5700": ("PC", "Слойка"), "5710": ("PC", "Булка"),
                  "5720": ("PC", "Хлеба"), "5730": ("PC", "Ручная зона ПЦ")}.get(rule["pf_cost_center"])
        if rule["buffer"] == "КЦ.Жаренные блюда":
            target = ("KC", "Жареные блюда")
    else:
        target = {"ПЦ": ("PC", "НАЧИНКА"), "КЦ": ("KC", "Горячий цех")}.get(rule["filling_workshop"])
    return next((line for line in lines if target == (line.workshop_code, line.name)), None)


def preview_expansion(db: Session, values: dict, *, scheduled: bool = False) -> dict:
    from app.services.plan_service import PlanService
    from app.services.pf_mapping_service import effective_mapping, mapping_revision

    product = db.get(Product, values["product_id"])
    line = db.get(ProductionLine, values["line_id"])
    if not product or not product.active or product.catalog_status != "active" or not line or line.status != "active":
        raise ValueError("Выберите действующие SKU и линию")
    cap = db.scalar(select(LineCapability).where(LineCapability.product_id == product.id, LineCapability.line_id == line.id))
    if not cap or cap.units_per_hour <= 0:
        raise ValueError("Для выбранного SKU не задана скорость на этой линии")
    quantity = Decimal(values["quantity"])
    if not scheduled:
        quantity = PlanService._round_quantity_for_product(quantity, product, cap)
    production_date = values["production_date"]
    if not scheduled and values.get("source_kind") == "ohl" and uses_sandwich_window(line):
        production_date = ohl_production_date(values.get("marking_date") or production_date, line, product)
    mapping = effective_mapping(db, product.sku, line.id)
    revision = mapping_revision(mapping)
    if values.get("pf_mapping_revision") and values["pf_mapping_revision"] != revision:
        raise ValueError("Связи ГП → ПФ изменены. Выключите и снова включите раскрутку для обновления предпросмотра")
    rules = [row for row in mapping["items"] if row.get("enabled", True)] if values.get("source_kind") != "pf" else []
    overrides_list = values.get("pf_tasks") or []
    overrides = {row["sku"]: row for row in overrides_list}
    if len(overrides) != len(overrides_list) or set(overrides) - {rule["sku"] for rule in rules}:
        raise ValueError("Состав ПФ изменён. Обновите предпросмотр")
    products = {rule["sku"]: db.scalar(select(Product).where(Product.sku == rule["sku"])) for rule in rules}
    volume, amounts = calculate_components(product, quantity, products) if rules else (None, {})
    calculation_revision = hashlib.sha256(json.dumps(
        {"sku": product.sku, "quantity_kg": quantity, "volume": volume, "amounts": amounts},
        sort_keys=True, default=str, ensure_ascii=False,
    ).encode()).hexdigest()
    if values.get("pf_calculation_revision") and values["pf_calculation_revision"] != calculation_revision:
        raise ValueError("Расчёт BOM изменился. Обновите предпросмотр ПФ перед созданием задания")
    lines = list(db.scalars(select(ProductionLine).where(ProductionLine.status == "active")))
    rows = []
    for rule in rules:
        override = overrides.get(rule["sku"], {})
        child = products[rule["sku"]]
        amount = amounts[rule["sku"]]
        capabilities = list(db.scalars(select(LineCapability).where(LineCapability.product_id == child.id))) if child else []
        target = _target_line(rule, lines)
        if override.get("line_id") is not None:
            target = next((row for row in lines if row.id == override["line_id"]), None)
        elif not target:
            candidates = [row for row in lines if any(c.line_id == row.id for c in capabilities)]
            target = candidates[0] if len(candidates) == 1 else None
        child_cap = next((c for c in capabilities if target and c.line_id == target.id), None)
        speed = override.get("speed_kg_hour")
        if speed is None:
            speed = rule.get("speed_kg_hour")
        if speed is None:
            speed = child_cap.units_per_hour if child_cap else None
        speed = Decimal(str(speed)) if speed is not None else None
        child_date = override.get("production_date") or production_date - timedelta(days=rule["lead_days"])
        if isinstance(child_date, str):
            child_date = date.fromisoformat(child_date)
        errors = []
        if child and (not child.active or child.catalog_status != "active"):
            errors.append("ПФ заблокирован или удалён в справочнике")
        if not target:
            errors.append("Выберите линию ПФ")
        if speed is None or not speed.is_finite() or speed <= 0:
            errors.append("Укажите скорость ПФ в кг/ч")
        if child_date > production_date:
            errors.append("Дата ПФ не может быть позже ГП")
        rows.append({"sku": rule["sku"], "name": rule["name"], "product_id": child.id if child else None,
                     "line_id": target.id if target else None, "line_name": target.name if target else None,
                     **amount, "production_date": child_date, "speed_kg_hour": speed,
                     "required_hours": PlanService._production_duration(amount["quantity_kg"], speed) if speed and speed.is_finite() and speed > 0 else None,
                     "note": rule["note"], "date_defaulted": rule.get("date_defaulted", False), "errors": errors})
    has_gp_speed = bool(rules and mapping.get("gp_speed_kg_hour"))
    gp_speed = Decimal(str(mapping["gp_speed_kg_hour"])) if has_gp_speed else Decimal(cap.units_per_hour)
    return {"items": rows, "quantity_kg": quantity, "quantity_units": volume, "production_date": production_date,
            "calculation_revision": calculation_revision, "quantity_source": "bom" if rules else None,
            "gp_speed_kg_hour": gp_speed, "gp_required_hours": PlanService._production_duration(quantity, gp_speed),
            "gp_speed_override": has_gp_speed, "mapping_revision": revision}


def append_expansion(db: Session, plan, parent: ProductionScheduleItem, preview: dict) -> None:
    from app.services.plan_service import PlanService

    # The complete set is validated before creating any products or tasks.
    errors = [f'{row["sku"]}: {error}' for row in preview["items"] for error in row["errors"]]
    if errors:
        raise ValueError("; ".join(errors))
    if preview["gp_speed_override"]:
        parent.pf_details = {"speed_kg_hour": str(preview["gp_speed_kg_hour"])}
        parent.required_hours = parent.duration_hours = preview["gp_required_hours"]
    db.flush()
    for row in preview["items"]:
        product = db.get(Product, row["product_id"]) if row["product_id"] else None
        if product is None:
            product = Product(sku=row["sku"], name=row["name"], unit="кг", reference_source=source_data()["source"])
            db.add(product)
            db.flush()
        cap = db.scalar(select(LineCapability).where(LineCapability.product_id == product.id, LineCapability.line_id == row["line_id"]))
        if not cap:
            db.add(LineCapability(product_id=product.id, line_id=row["line_id"], units_per_hour=row["speed_kg_hour"], speed_unit="кг/час"))
        quantity = row["quantity_kg"]
        plan.schedule_items.append(ProductionScheduleItem(
            parent_task_id=parent.id, product=product, line_id=row["line_id"],
            production_date=row["production_date"], marking_date=row["production_date"], shift=parent.shift,
            sequence=len(plan.schedule_items) + 1, quantity=quantity, quantity_kg=quantity,
            source_quantity=row["source_quantity"], source_unit=row["source_unit"], box_count=PlanService._box_count(quantity, product),
            required_hours=row["required_hours"], duration_hours=row["required_hours"],
            source_kind="pf", schedule_kind="production", source="manual", locked=True,
            status=ScheduleStatus.PLANNED, warnings=[],
            reason=f"ПФ для ГП {parent.product.sku}, задание {parent.id}",
            pf_details={"speed_kg_hour": str(row["speed_kg_hour"]), "source_row_sku": row["sku"],
                        "quantity_source": "bom", "gp_quantity_units": str(preview["quantity_units"])},
        ))


def sync_children(plan, parent, previous_date, quantity_changed: bool) -> None:
    """Keep the explicitly created chain together on parent edits; never re-expand."""
    from app.services.plan_service import PlanService

    children = [row for row in plan.schedule_items if row.parent_task_id == parent.id and not row.excluded]
    for child in children:
        if child.execution_status not in {None, "not_started"}:
            raise ValueError("Связанный ПФ уже передан в работу. Сначала скорректируйте его отдельно")
    bom_children = [child for child in children if (child.pf_details or {}).get("quantity_source") == "bom"]
    amounts = {}
    if bom_children and quantity_changed and not parent.excluded:
        volume, amounts = calculate_components(parent.product, Decimal(parent.quantity_kg),
                                              {child.product.sku: child.product for child in bom_children})
    for child in children:
        if parent.excluded:
            child.excluded = True
        elif quantity_changed:
            amount = amounts.get(child.product.sku) if child in bom_children else None
            child.quantity = child.quantity_kg = amount["quantity_kg"] if amount else parent.quantity_kg
            child.source_quantity = amount["source_quantity"] if amount else parent.quantity_kg
            child.source_unit = amount["source_unit"] if amount else "кг"
            if amount:
                child.pf_details = {**child.pf_details, "gp_quantity_units": str(volume)}
            child.box_count = PlanService._box_count(child.quantity, child.product)
            speed = Decimal(child.pf_details["speed_kg_hour"])
            child.required_hours = child.duration_hours = PlanService._production_duration(child.quantity, speed)
        if previous_date and parent.production_date and child.production_date:
            child.production_date += parent.production_date - previous_date
            child.marking_date = child.production_date


def ensure_task_calendar(db: Session, plan) -> None:
    from app.services.line_schedule_service import ensure_line_capacities

    rows = [row for row in plan.schedule_items if row.production_date and row.line_id and not row.excluded]
    if not rows:
        return
    plan.horizon_start = min(plan.horizon_start, *(row.production_date for row in rows))
    plan.horizon_end = max(plan.horizon_end, *(row.production_date for row in rows))
    for line_id in {row.line_id for row in rows}:
        dates = [row.production_date for row in rows if row.line_id == line_id]
        ensure_line_capacities(db, [db.get(ProductionLine, line_id)], min(dates), max(dates))
