from datetime import timedelta
from decimal import Decimal
from hashlib import sha1
import re

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.security import UserContext, require_planner
from app.models.entities import (
    AuditEvent, DemandItem, ImportedOrder, ImportFile, LineCapability, LineCapacity, Product,
    ProductionLine, ProductionPlan,
)
from app.schemas.common import ImportConfirmRequest, ImportPreview
from app.services.import_service import ExcelImportService
from app.services.line_schedule_service import ensure_line_capacities
from app.services.plan_service import PlanService, plan_dict
from app.services.notification_service import send_notification

router = APIRouter(prefix="/imports", tags=["imports"])


@router.post("/preview", response_model=ImportPreview)
async def preview_import(file: UploadFile = File(...), user: UserContext = Depends(require_planner)) -> ImportPreview:
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xlsm")):
        raise HTTPException(400, "Поддерживаются файлы XLSX и XLSM")
    content = await file.read()
    if len(content) > 30 * 1024 * 1024:
        raise HTTPException(413, "Файл превышает 30 МБ")
    try:
        return ExcelImportService().parse(content, file.filename)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/confirm")
def confirm_import(payload: ImportConfirmRequest, db: Session = Depends(get_db), user: UserContext = Depends(require_planner)) -> dict:
    preview = payload.preview
    if not preview.valid_rows:
        raise HTTPException(422, "В файле нет корректных строк")
    order = ImportedOrder(
        source_name=preview.file_name, status="confirmed", mapping_code=preview.mapping_code,
        template_type=preview.template_type,
        total_rows=preview.total_rows, valid_rows=preview.valid_rows, invalid_rows=preview.invalid_rows,
    )
    db.add(order)
    db.flush()
    db.add(ImportFile(imported_order_id=order.id, original_name=preview.file_name))
    product_by_sku = {product.sku: product for product in db.scalars(select(Product))}

    if preview.template_type in {"production_reference", "legacy_reference", "capacity_reference"}:
        updated = 0
        for row in preview.rows:
            if not row.valid:
                continue
            product = _upsert_product(db, product_by_sku, row)
            if preview.template_type in {"production_reference", "capacity_reference"}:
                _upsert_capability(db, product, row)
            updated += 1
        order.status = "reference_imported"
        db.add(AuditEvent(
            username=user.username, action="reference_imported", entity_type="imported_order",
            entity_id=str(order.id), details={"file_name": preview.file_name, "template_type": preview.template_type, "updated": updated},
        ))
        db.commit()
        send_notification(
            db, "reference_imported", f"PLAN Portal: загружен справочник {preview.file_name}",
            f"Пользователь {user.display_name} обновил справочник. Обработано строк: {updated}.",
        )
        return {"order_id": order.id, "plan": None, "reference_updated": updated}

    source_kind = {"ohl_daily": "ohl", "quarter_weekly": "zam"}.get(preview.template_type, "generic")
    demands = []
    for row in preview.rows:
        if not row.valid or row.quantity is None or row.requested_date is None or row.due_date is None:
            continue
        product = _upsert_product(db, product_by_sku, row)
        _upsert_capability(db, product, row)
        demand = DemandItem(
            order_id=order.id, product_id=product.id, source_row=row.row_number, sku=row.sku,
            product_name=row.product_name or product.name, quantity=row.quantity,
            source_quantity=row.source_quantity, source_unit=row.source_unit,
            quantity_kg=row.quantity_kg or row.quantity, box_count=row.box_count,
            production_week=row.production_week, exact_date=row.exact_date,
            source_kind=source_kind, source_plan_date=row.source_plan_date or row.requested_date,
            marking_date=row.marking_date, advance_production=row.advance_marking,
            requested_date=row.requested_date, due_date=row.due_date, priority=row.priority,
            customer=row.customer, valid=True, validation_errors=row.warnings,
            raw_data={"template_type": preview.template_type, "line_hint": row.line_hint, "advance_marking": row.advance_marking},
        )
        db.add(demand)
        demands.append(demand)
    db.flush()
    if not payload.create_plan:
        db.add(AuditEvent(
            username=user.username, action="demand_imported", entity_type="imported_order",
            entity_id=str(order.id), details={"file_name": preview.file_name, "rows": len(demands), "plan_created": False},
        ))
        db.commit()
        return {"order_id": order.id, "plan": None}
    all_demands = _latest_real_demands(db) if payload.merge_into_active and source_kind in {"ohl", "zam"} else demands
    horizon_start = min(item.requested_date for item in all_demands)
    horizon_end = max(item.due_date for item in all_demands)
    _ensure_shift_capacities(db, all_demands, horizon_start, horizon_end)
    plan = PlanService(db).active_plan()
    if not (payload.merge_into_active and plan and plan.name.startswith("Комплексный план ФК")):
        for existing in db.scalars(select(ProductionPlan).where(ProductionPlan.active.is_(True))):
            existing.active = False
        plan = ProductionPlan(name="Комплексный план ФК · ОХЛ + ЗАМ", horizon_start=horizon_start, horizon_end=horizon_end, active=True)
        db.add(plan)
        db.flush()
    else:
        plan.horizon_start = horizon_start
        plan.horizon_end = horizon_end
    PlanService(db).calculate(plan, all_demands, f"import_{source_kind}")
    db.add(AuditEvent(
        username=user.username, action="plan_imported", entity_type="production_plan", entity_id=str(plan.id),
        details={"file_name": preview.file_name, "source_kind": source_kind, "rows": len(demands)},
    ))
    db.commit()
    send_notification(
        db, "plan_imported", f"PLAN Portal: обновлён план {plan.name}",
        f"Пользователь {user.display_name} загрузил файл {preview.file_name}.\nИсточник: {source_kind.upper()}.\nСтрок: {len(demands)}.",
    )
    return {"order_id": order.id, "plan": plan_dict(db, plan)}


def _upsert_product(db: Session, product_by_sku: dict[str, Product], row) -> Product:
    product = product_by_sku.get(row.sku)
    if not product:
        product = Product(sku=row.sku, name=row.product_name or row.sku)
        db.add(product)
        db.flush()
        product_by_sku[row.sku] = product
    if row.product_name and (row.product_name != row.sku or product.name == product.sku):
        product.name = row.product_name
    product.unit = row.source_unit or product.unit
    for field in ("unit_weight_kg", "units_per_box", "box_weight_kg", "state", "category", "short_name"):
        value = getattr(row, field, None)
        if value is not None:
            setattr(product, field, value)
    for field in ("legacy_quantum_units", "legacy_daily_capacity_units", "legacy_capacity_unit", "reference_source"):
        value = getattr(row, field, None)
        if value is not None:
            setattr(product, field, value)
    if getattr(row, "recipe_component_count", 0):
        product.recipe_component_count = row.recipe_component_count
    return product


def _upsert_capability(db: Session, product: Product, row) -> LineCapability | None:
    if not row.line_hint or not row.speed_kg_hour:
        return None
    line = db.scalar(select(ProductionLine).where(ProductionLine.name == row.line_hint))
    if not line:
        clean = re.sub(r"[^0-9A-ZА-Я]+", "-", row.line_hint.upper()).strip("-")[:24] or "LINE"
        digest = sha1(row.line_hint.encode("utf-8")).hexdigest()[:6].upper()
        workshop_code, workshop_name = _workshop_for_line(row.line_hint)
        line = ProductionLine(
            code=f"FK-{clean}-{digest}", name=row.line_hint, working_hours=22,
            workshop_code=workshop_code, workshop_name=workshop_name,
            default_capacity=Decimal(row.speed_kg_hour) * Decimal("22"), capacity_unit="кг/день",
            comments="Импортировано из справочника ФК · 22 ч производства + 2 ч обеда",
        )
        db.add(line)
        db.flush()
    else:
        line.workshop_code, line.workshop_name = _workshop_for_line(line.name)
    capability = db.scalar(select(LineCapability).where(
        LineCapability.line_id == line.id, LineCapability.product_id == product.id,
    ))
    if not capability:
        capability = LineCapability(line_id=line.id, product_id=product.id, units_per_hour=row.speed_kg_hour)
        db.add(capability)
        # One source row is created per date, so the same SKU/line pair repeats many
        # times in OHL. Flush now to make the new pair visible to the next lookup.
        db.flush()
    capability.units_per_hour = row.speed_kg_hour
    capability.speed_unit = "кг/час"
    capability.batch_quantum_kg = row.batch_quantum_kg
    capability.min_order_kg = row.min_order_kg
    capability.capacity_type = row.capacity_type
    capability.restrictions = row.restrictions
    capability.min_batch = row.batch_quantum_kg
    if getattr(row, "available_hours", None) is not None or getattr(row, "line_status", None):
        capability.technological_constraints = {
            **(capability.technological_constraints or {}),
            "source_available_hours": str(row.available_hours) if row.available_hours is not None else None,
            "source_line_status": row.line_status,
        }
    return capability


def _ensure_shift_capacities(db: Session, demands: list[DemandItem], start, end) -> None:
    product_ids = {item.product_id for item in demands if item.product_id}
    line_ids = set(db.scalars(select(LineCapability.line_id).where(LineCapability.product_id.in_(product_ids))))
    lines = list(db.scalars(select(ProductionLine).where(ProductionLine.id.in_(line_ids))))
    ensure_line_capacities(db, lines, start, end)


WORKSHOP_LINES = {
    "PC": ("ПЦ", {"Булка", "Слойка", "Хлеба", "Ручная зона ПЦ", "Сухари"}),
    "KC": ("КЦ", {"Сэндвичи", "Жареные блюда", "Лазанья", "Миквак", "Бургеры", "Супы", "Салаты", "Напитки", "Ручная зона КЦ"}),
}


def _workshop_for_line(line_name: str) -> tuple[str, str]:
    normalized = line_name.strip().lower().replace("линия ", "")
    for code, (name, lines) in WORKSHOP_LINES.items():
        if any(normalized == item.lower() or item.lower() in normalized for item in lines):
            return code, name
    return "UNASSIGNED", "Не распределено"


def _latest_real_demands(db: Session) -> list[DemandItem]:
    orders = list(db.scalars(select(ImportedOrder).where(
        ImportedOrder.template_type.in_(("ohl_daily", "quarter_weekly")),
    ).order_by(ImportedOrder.imported_at.desc(), ImportedOrder.id.desc())))
    latest_ids: list[int] = []
    seen: set[tuple[str, str]] = set()
    for order in orders:
        key = (order.source_name, order.template_type)
        if key not in seen:
            latest_ids.append(order.id)
            seen.add(key)
    if not latest_ids:
        return []
    return list(db.scalars(select(DemandItem).where(DemandItem.order_id.in_(latest_ids), DemandItem.valid.is_(True))))
