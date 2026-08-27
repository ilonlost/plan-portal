from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.models.entities import (
    DemandItem, LineCapability, LineCapacity, PlanStatus, ProductionLine, ProductionPlan,
    ProductionPlanVersion, ProductionScheduleItem, ScheduleStatus,
)
from app.services.planning_engine import CapabilityInput, CapacityInput, DemandInput, PlanningEngine


class PlanService:
    def __init__(self, db: Session):
        self.db = db

    def active_plan(self) -> ProductionPlan | None:
        return self.db.scalar(
            select(ProductionPlan).where(ProductionPlan.active.is_(True)).order_by(ProductionPlan.updated_at.desc())
        )

    def calculate(self, plan: ProductionPlan, demands: list[DemandItem], change_type: str = "automatic_calculation") -> ProductionPlan:
        capabilities = list(self.db.scalars(select(LineCapability).options(joinedload(LineCapability.line))))
        capacities = list(self.db.scalars(select(LineCapacity)))
        result = PlanningEngine().plan(
            demands=[DemandInput(
                id=item.id, product_id=item.product_id or 0, sku=item.sku, quantity=Decimal(item.quantity),
                requested_date=item.requested_date, due_date=item.due_date, priority=item.priority,
                source_quantity=Decimal(item.source_quantity) if item.source_quantity is not None else None,
                source_unit=item.source_unit,
                unit_weight_kg=Decimal(item.product.unit_weight_kg) if item.product and item.product.unit_weight_kg else None,
                units_per_box=Decimal(item.product.units_per_box) if item.product and item.product.units_per_box else None,
                box_quantum_kg=Decimal(item.product.box_weight_kg) if item.product and item.product.box_weight_kg else None,
                exact_date=item.exact_date, source_kind=item.source_kind, marking_date=item.marking_date,
                warnings=tuple(item.validation_errors or []),
            ) for item in demands if item.valid],
            capabilities=[CapabilityInput(
                line_id=item.line_id, product_id=item.product_id, units_per_hour=Decimal(item.units_per_hour),
                line_priority=item.line.priority,
                batch_quantum_kg=Decimal(item.batch_quantum_kg) if item.batch_quantum_kg else None,
                min_order_kg=Decimal(item.min_order_kg) if item.min_order_kg else None,
            ) for item in capabilities],
            capacities=[CapacityInput(
                line_id=item.line_id, capacity_date=item.capacity_date,
                available_hours=Decimal(item.available_hours), available=item.available, shift=item.shift,
            ) for item in capacities],
            horizon_end=plan.horizon_end,
        )
        for existing in list(plan.schedule_items):
            self.db.delete(existing)
        self.db.flush()
        for index, item in enumerate(result, start=1):
            plan.schedule_items.append(ProductionScheduleItem(
                demand_item_id=item.demand_id, product_id=item.product_id, line_id=item.line_id,
                production_date=item.production_date, sequence=index, quantity=item.quantity,
                source_quantity=item.source_quantity, source_unit=item.source_unit,
                quantity_kg=item.quantity_kg, box_count=item.box_count, batch_count=item.batch_count,
                source_kind=item.source_kind, marking_date=item.marking_date,
                shift=item.shift,
                required_hours=item.required_hours, status=ScheduleStatus(item.status),
                source="auto", warnings=item.warnings,
            ))
        plan.status = PlanStatus.NEEDS_REVIEW if any(item.status in {"conflict", "unscheduled"} for item in result) else PlanStatus.CALCULATED
        self.db.flush()
        self.recalculate_load(plan)
        self.create_version(plan, change_type, "Автоматический расчёт по правилам конечной мощности")
        self.db.commit()
        self.db.refresh(plan)
        return plan

    def recalculate_load(self, plan: ProductionPlan) -> None:
        lines = {line.id: line for line in self.db.scalars(select(ProductionLine))}
        capacity_rows = {(item.line_id, item.capacity_date, item.shift): Decimal(item.available_hours) for item in self.db.scalars(select(LineCapacity)) if item.available}
        groups: dict[tuple[int, date, str], list[ProductionScheduleItem]] = defaultdict(list)
        for item in plan.schedule_items:
            if item.line_id and item.production_date and not item.excluded:
                groups[(item.line_id, item.production_date, item.shift)].append(item)
        for (line_id, production_date, shift), items in groups.items():
            capacity = capacity_rows.get((line_id, production_date, shift), Decimal(lines[line_id].working_hours) / Decimal("2"))
            total_hours = sum((Decimal(item.required_hours) for item in items), Decimal("0"))
            load = (total_hours / capacity * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if capacity else Decimal("999")
            for item in items:
                warnings = [warning for warning in (item.warnings or []) if not warning.startswith("Загрузка линии")]
                item.load_percent = load
                if load > 100:
                    item.status = ScheduleStatus.CONFLICT
                    warnings.append(f"Загрузка линии {load}% превышает доступную мощность")
                elif load >= 85 and item.status != ScheduleStatus.CONFLICT:
                    item.status = ScheduleStatus.WARNING
                    warnings.append(f"Загрузка линии близка к пределу: {load}%")
                elif item.status not in {ScheduleStatus.CONFLICT, ScheduleStatus.UNSCHEDULED}:
                    item.status = ScheduleStatus.PLANNED
                item.warnings = warnings
        for item in plan.schedule_items:
            if item.line_id is None or item.production_date is None:
                item.load_percent = 0
                item.status = ScheduleStatus.UNSCHEDULED
        plan.status = PlanStatus.NEEDS_REVIEW if any(item.status in {ScheduleStatus.CONFLICT, ScheduleStatus.UNSCHEDULED} for item in plan.schedule_items) else PlanStatus.CALCULATED
        self.db.flush()

    def update_item(self, item: ProductionScheduleItem, values: dict) -> ProductionPlan:
        plan = item.plan
        for field in ("production_date", "line_id", "shift", "locked", "excluded"):
            if field in values and values[field] is not None:
                setattr(item, field, values[field])
        if values.get("quantity") is not None:
            item.quantity = Decimal(values["quantity"])
            item.quantity_kg = item.quantity
        if item.schedule_kind == "production" and item.line_id and item.product_id:
            capability = self.db.scalar(select(LineCapability).where(
                LineCapability.line_id == item.line_id, LineCapability.product_id == item.product_id,
            ))
            if capability and Decimal(capability.units_per_hour) > 0:
                item.required_hours = (Decimal(item.quantity) / Decimal(capability.units_per_hour)).quantize(Decimal("0.01"))
        item.source = "manual"
        self.recalculate_load(plan)
        self.create_version(plan, "manual_change", values.get("comment") or "Ручная корректировка задания")
        self.db.commit()
        self.db.refresh(item)
        return plan

    def create_event(self, plan: ProductionPlan, values: dict) -> ProductionPlan:
        kind = values["schedule_kind"]
        if kind not in {"downtime", "maintenance", "trial"}:
            raise ValueError("Допустимы события: downtime, maintenance, trial")
        event = ProductionScheduleItem(
            plan_id=plan.id, product_id=None, line_id=values["line_id"],
            production_date=values["production_date"], shift=values.get("shift", "day"),
            sequence=len(plan.schedule_items) + 1, quantity=Decimal("0"), quantity_kg=Decimal("0"),
            required_hours=Decimal(values["duration_hours"]), duration_hours=Decimal(values["duration_hours"]),
            schedule_kind=kind, reason=values["reason"], source="manual", locked=True,
            status=ScheduleStatus.PLANNED,
        )
        plan.schedule_items.append(event)
        self.db.flush()
        self.recalculate_load(plan)
        self.create_version(plan, f"manual_{kind}", f"{kind}: {values['reason']}")
        self.db.commit()
        return plan

    def delete_item(self, item: ProductionScheduleItem) -> ProductionPlan:
        plan = item.plan
        description = item.reason or (item.product.name if item.product else "задание")
        self.db.delete(item)
        self.db.flush()
        self.recalculate_load(plan)
        self.create_version(plan, "manual_delete", f"Удалено: {description}")
        self.db.commit()
        return plan

    def approve(self, plan: ProductionPlan, comment: str | None = None) -> ProductionPlan:
        if any(item.status in {ScheduleStatus.CONFLICT, ScheduleStatus.UNSCHEDULED} for item in plan.schedule_items):
            raise ValueError("Нельзя утвердить план с конфликтами или нераспределёнными заданиями")
        plan.status = PlanStatus.APPROVED
        self.create_version(plan, "approval", comment or "План утверждён")
        self.db.commit()
        return plan

    def update_execution_status(self, item: ProductionScheduleItem, status: str, note: str | None, username: str) -> ProductionPlan:
        allowed = {"not_started", "in_progress", "completed", "partially_shipped", "not_shipped"}
        if status not in allowed:
            raise ValueError("Неизвестный статус исполнения")
        if status in {"not_shipped", "partially_shipped"} and not (note or "").strip():
            raise ValueError("Для неотгрузки или частичной отгрузки укажите причину")
        item.execution_status = status
        item.execution_note = (note or "").strip() or None
        item.reported_by = username
        item.reported_at = datetime.now(timezone.utc)
        self.create_version(item.plan, "execution_status", f"{item.product.name if item.product else 'Задание'}: {status}. {item.execution_note or ''}".strip())
        self.db.commit()
        return item.plan

    def create_version(self, plan: ProductionPlan, change_type: str, comment: str) -> None:
        current = self.db.scalar(select(func.max(ProductionPlanVersion.version_number)).where(ProductionPlanVersion.plan_id == plan.id)) or 0
        snapshot = {"status": plan.status.value, "items": [
            {"id": item.id, "date": item.production_date.isoformat() if item.production_date else None, "line_id": item.line_id,
             "quantity": str(item.quantity), "status": item.status.value, "locked": item.locked}
            for item in plan.schedule_items
        ]}
        self.db.add(ProductionPlanVersion(plan_id=plan.id, version_number=current + 1, change_type=change_type, comment=comment, snapshot=snapshot))


def schedule_item_dict(item: ProductionScheduleItem) -> dict:
    quantity_units = None
    if item.source_unit == "шт" and item.source_quantity is not None:
        quantity_units = item.source_quantity
    elif item.product:
        unit_weight = Decimal(item.product.unit_weight_kg or 0)
        if unit_weight <= 0 and item.product.box_weight_kg and item.product.units_per_box:
            unit_weight = Decimal(item.product.box_weight_kg) / Decimal(item.product.units_per_box)
        if unit_weight > 0:
            quantity_units = (Decimal(item.quantity_kg or item.quantity or 0) / unit_weight).quantize(Decimal("0.001"))
    return {
        "id": item.id,
        "production_date": item.production_date,
        "line_id": item.line_id,
        "line_code": item.line.code if item.line else None,
        "line_name": item.line.name if item.line else None,
        "workshop_code": item.line.workshop_code if item.line else None,
        "workshop_name": item.line.workshop_name if item.line else None,
        "product_id": item.product_id,
        "product_name": item.product.name if item.product else ({"downtime": "Простой", "maintenance": "ТО", "trial": "Проработка"}.get(item.schedule_kind, "Служебное событие")),
        "sku": item.product.sku if item.product else "—",
        "quantity": item.quantity,
        "source_quantity": item.source_quantity,
        "source_unit": item.source_unit,
        "quantity_kg": item.quantity_kg,
        "quantity_units": quantity_units,
        "box_count": item.box_count,
        "batch_count": item.batch_count,
        "schedule_kind": item.schedule_kind,
        "duration_hours": item.duration_hours,
        "reason": item.reason,
        "actual_quantity_kg": item.actual_quantity_kg,
        "source_kind": item.source_kind,
        "marking_date": item.marking_date,
        "execution_status": item.execution_status,
        "execution_note": item.execution_note,
        "reported_by": item.reported_by,
        "reported_at": item.reported_at,
        "shift": item.shift,
        "required_hours": item.required_hours,
        "load_percent": item.load_percent,
        "status": item.status.value,
        "source": item.source,
        "locked": item.locked,
        "excluded": item.excluded,
        "due_date": item.demand_item.due_date if item.demand_item else None,
        "warnings": item.warnings or [],
    }


def plan_dict(db: Session, plan: ProductionPlan, allowed_line_name: str | None = None) -> dict:
    items = list(db.scalars(
        select(ProductionScheduleItem)
        .where(ProductionScheduleItem.plan_id == plan.id)
        .options(joinedload(ProductionScheduleItem.product), joinedload(ProductionScheduleItem.line), joinedload(ProductionScheduleItem.demand_item))
        .order_by(ProductionScheduleItem.production_date, ProductionScheduleItem.sequence)
    ))
    if allowed_line_name:
        items = [item for item in items if item.line and item.line.name == allowed_line_name]
    version = db.scalar(select(func.max(ProductionPlanVersion.version_number)).where(ProductionPlanVersion.plan_id == plan.id)) or 0
    statuses = defaultdict(int)
    for item in items:
        statuses[item.status.value] += 1
    return {
        "id": plan.id, "name": plan.name, "status": plan.status.value,
        "horizon_start": plan.horizon_start, "horizon_end": plan.horizon_end,
        "updated_at": plan.updated_at, "version": version,
        "items": [schedule_item_dict(item) for item in items],
        "summary": {
            "total": len(items), "planned": statuses["planned"] + statuses["warning"],
            "warnings": statuses["warning"], "conflicts": statuses["conflict"],
            "unscheduled": statuses["unscheduled"],
        },
    }
