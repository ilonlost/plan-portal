from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.core.security import UserContext, current_user, require_planner
from app.db.session import get_db
from app.models.entities import AuditEvent, ImportedOrder, LineCapability, Product, ProductionLine
from app.services.catalog_workbook_service import export_catalog, import_catalog


router = APIRouter(prefix="/catalog", tags=["catalog"])


class CapabilityUpdate(BaseModel):
    line_id: int | None = Field(default=None, gt=0)
    line_status: str | None = Field(default=None, max_length=120)
    advance_status: str | None = None
    fk_status: str | None = Field(default=None, max_length=120)
    product_name: str | None = Field(default=None, min_length=1, max_length=200)
    unit_weight_kg: Decimal | None = Field(default=None, gt=0)
    box_weight_kg: Decimal | None = Field(default=None, gt=0)
    units_per_box: Decimal | None = Field(default=None, gt=0)
    state: str | None = Field(default=None, max_length=40)
    category: str | None = Field(default=None, max_length=120)
    units_per_hour: Decimal | None = Field(default=None, gt=0)
    batch_quantum_kg: Decimal | None = Field(default=None, gt=0)
    min_order_kg: Decimal | None = Field(default=None, ge=0)
    restrictions: str | None = Field(default=None, max_length=1000)
    mono_group: str | None = Field(default=None, max_length=160)


class ProductUpdate(BaseModel):
    sku: str | None = Field(default=None, min_length=1, max_length=80)
    product_name: str | None = Field(default=None, min_length=1, max_length=200)
    state: str | None = Field(default=None, max_length=40)
    advance_status: str | None = Field(default=None, max_length=40)
    fk_status: str | None = Field(default=None, max_length=120)
    unit_weight_kg: Decimal | None = Field(default=None, gt=0)
    units_per_box: Decimal | None = Field(default=None, gt=0)
    box_weight_kg: Decimal | None = Field(default=None, gt=0)
    capability_id: int | None = Field(default=None, gt=0)
    line_id: int | None = Field(default=None, gt=0)
    units_per_hour: Decimal | None = Field(default=None, gt=0)
    batch_quantum_kg: Decimal | None = Field(default=None, gt=0)
    min_order_kg: Decimal | None = Field(default=None, ge=0)
    restrictions: str | None = Field(default=None, max_length=1000)
    mono_group: str | None = Field(default=None, max_length=160)


class ProductCreate(ProductUpdate):
    sku: str = Field(min_length=1, max_length=80)
    product_name: str = Field(min_length=1, max_length=200)


class ProductStatusUpdate(BaseModel):
    status: str


def _capability_dict(item: LineCapability) -> dict:
    return {
        "capability_id": item.id, "line_id": item.line.id, "line_name": item.line.name,
        "workshop_code": item.line.workshop_code, "workshop_name": item.line.workshop_name,
        "speed_kg_hour": item.units_per_hour, "batch_quantum_kg": item.batch_quantum_kg,
        "min_order_kg": item.min_order_kg, "restrictions": item.restrictions,
    }


@router.get("/manual-products")
def manual_products(line_id: int, db: Session = Depends(get_db), user: UserContext = Depends(require_planner)) -> list[dict]:
    rows = list(db.scalars(
        select(LineCapability).join(LineCapability.product).join(LineCapability.line).where(
            LineCapability.line_id == line_id,
            Product.active.is_(True),
            ProductionLine.status == "active",
        )
        .options(joinedload(LineCapability.product)).order_by(Product.name)
    ))
    return [{"product_id": row.product_id, "sku": row.product.sku, "name": row.product.name, "speed_kg_hour": row.units_per_hour} for row in rows]


@router.get("")
def catalog(
    workshop_code: str | None = None, line_id: int | None = None, search: str | None = None,
    limit: int = Query(default=750, ge=1, le=2000), db: Session = Depends(get_db),
    user: UserContext = Depends(current_user),
) -> dict:
    query = select(LineCapability).options(
        joinedload(LineCapability.product), joinedload(LineCapability.line),
    ).join(LineCapability.product).join(LineCapability.line).where(
        Product.catalog_status != "deleted", ProductionLine.status == "active",
    )
    if workshop_code:
        query = query.where(ProductionLine.workshop_code == workshop_code)
    if line_id:
        query = query.where(ProductionLine.id == line_id)
    if search:
        pattern = f"%{search.strip()}%"
        query = query.where(or_(Product.sku.ilike(pattern), Product.name.ilike(pattern), ProductionLine.name.ilike(pattern)))
    capabilities = list(db.scalars(query.order_by(ProductionLine.workshop_code, ProductionLine.name, Product.name).limit(limit)))
    products = list(db.scalars(
        select(Product).where(Product.catalog_status != "deleted").options(
            joinedload(Product.capabilities).joinedload(LineCapability.line),
        ).order_by(Product.sku)
    ).unique())
    products_total = len(products)
    sources = list(db.scalars(select(ImportedOrder).order_by(ImportedOrder.imported_at.desc()).limit(20)))
    product_rows = []
    unmapped_products = []
    for product in products:
        active_capabilities = [capability for capability in product.capabilities if capability.line and capability.line.status == "active"]
        line_names = sorted({capability.line.name for capability in active_capabilities})
        product_rows.append({
            "product_id": product.id, "sku": product.sku, "product_name": product.name,
            "state": product.state, "category": product.category,
            "advance_status": product.advance_status, "fk_status": product.fk_status,
            "unit_weight_kg": product.unit_weight_kg, "units_per_box": product.units_per_box,
            "box_weight_kg": product.box_weight_kg,
            "mono_group": product.mono_group,
            "active": product.active, "catalog_status": product.catalog_status,
            "capability_count": len(active_capabilities), "line_names": line_names,
            "capabilities": [_capability_dict(item) for item in active_capabilities],
        })
        if not active_capabilities:
            unmapped_products.append({"product_id": product.id, "sku": product.sku, "product_name": product.name})
    return {
        "products": product_rows,
        "unmapped_products": unmapped_products,
        "summary": {
            "products": products_total,
            "capabilities": db.scalar(
                select(func.count(LineCapability.id)).join(LineCapability.product).join(LineCapability.line).where(
                    Product.catalog_status != "deleted", ProductionLine.status == "active",
                )
            ) or 0,
            "lines": db.scalar(select(func.count(ProductionLine.id)).where(ProductionLine.status == "active")) or 0,
            "with_recipes": db.scalar(select(func.count(Product.id)).where(
                Product.catalog_status != "deleted", Product.recipe_component_count > 0,
            )) or 0,
        },
        "rows": [{
            "capability_id": item.id,
            "product_id": item.product.id, "sku": item.product.sku, "product_name": item.product.name,
            "state": item.product.state, "category": item.product.category,
            "advance_status": item.product.advance_status, "fk_status": item.product.fk_status,
            "line_status": (item.technological_constraints or {}).get("source_line_status"),
            "unit_weight_kg": item.product.unit_weight_kg, "units_per_box": item.product.units_per_box,
            "box_weight_kg": item.product.box_weight_kg,
            "workshop_code": item.line.workshop_code, "workshop_name": item.line.workshop_name,
            "line_id": item.line.id, "line_name": item.line.name,
            "speed_kg_hour": item.units_per_hour, "batch_quantum_kg": item.batch_quantum_kg,
            "min_order_kg": item.min_order_kg, "capacity_type": item.capacity_type,
            "restrictions": item.restrictions,
            "legacy_quantum_units": item.product.legacy_quantum_units,
            "legacy_daily_capacity_units": item.product.legacy_daily_capacity_units,
            "legacy_capacity_unit": item.product.legacy_capacity_unit,
            "recipe_component_count": item.product.recipe_component_count,
            "reference_source": item.product.reference_source,
            "mono_group": item.product.mono_group,
            "active": item.product.active, "catalog_status": item.product.catalog_status,
        } for item in capabilities],
        "sources": [{
            "id": item.id, "file_name": item.source_name, "template_type": item.template_type,
            "status": item.status, "total_rows": item.total_rows, "valid_rows": item.valid_rows,
            "invalid_rows": item.invalid_rows, "imported_at": item.imported_at,
        } for item in sources],
    }


def _product_values(product: Product, values: dict) -> None:
    if "sku" in values:
        product.sku = values.pop("sku").strip()
    if "product_name" in values:
        product.name = values.pop("product_name").strip()
    for field in ("state", "advance_status", "fk_status", "unit_weight_kg", "units_per_box", "box_weight_kg"):
        if field in values:
            setattr(product, field, values.pop(field))


def _save_product_capability(db: Session, product: Product, values: dict) -> LineCapability | None:
    capability_id = values.pop("capability_id", None)
    line_id = values.pop("line_id", None)
    capability_fields = {key: values.pop(key) for key in list(values) if key in {
        "units_per_hour", "batch_quantum_kg", "min_order_kg", "restrictions",
    }}
    if "mono_group" in values:
        product.mono_group = (values.pop("mono_group") or "").strip() or None
    capability = db.get(LineCapability, capability_id) if capability_id else None
    if capability and capability.product_id != product.id:
        raise HTTPException(422, "Связь линии относится к другому артикулу")
    if line_id:
        line = db.get(ProductionLine, line_id)
        if not line or line.status != "active":
            raise HTTPException(422, "Выберите существующую производственную линию")
        duplicate = db.scalar(select(LineCapability).where(
            LineCapability.product_id == product.id, LineCapability.line_id == line_id,
            *( [LineCapability.id != capability.id] if capability else [] ),
        ))
        if duplicate:
            raise HTTPException(409, "Связь этого артикула и линии уже существует")
        if capability:
            capability.line_id = line_id
        elif capability_fields.get("units_per_hour"):
            capability = LineCapability(product=product, line_id=line_id, units_per_hour=capability_fields["units_per_hour"])
            db.add(capability)
    if capability:
        for field, value in capability_fields.items():
            setattr(capability, field, value)
        if Decimal(capability.units_per_hour) <= 0:
            raise HTTPException(422, "Скорость должна быть положительным числом")
    elif capability_fields:
        raise HTTPException(422, "Для производственных параметров выберите линию")
    return capability


@router.post("/products")
def create_product(
    payload: ProductCreate, db: Session = Depends(get_db), user: UserContext = Depends(require_planner),
) -> dict:
    values = payload.model_dump(exclude_unset=True)
    sku = values.get("sku", "").strip()
    if db.scalar(select(Product).where(func.lower(Product.sku) == sku.lower())):
        raise HTTPException(409, "Артикул уже существует")
    product = Product(sku=sku, name=values.get("product_name", sku).strip(), active=True, catalog_status="active")
    db.add(product)
    db.flush()
    _product_values(product, values)
    capability = _save_product_capability(db, product, values)
    db.add(AuditEvent(username=user.username, action="product_created", entity_type="product", entity_id=str(product.id), details={"sku": product.sku}))
    db.commit()
    return {"ok": True, "product_id": product.id, "capability_id": capability.id if capability else None}


@router.patch("/products/{product_id}")
def update_product(
    product_id: int, payload: ProductUpdate, db: Session = Depends(get_db), user: UserContext = Depends(require_planner),
) -> dict:
    product = db.get(Product, product_id)
    if not product or product.catalog_status == "deleted":
        raise HTTPException(404, "Артикул не найден")
    values = payload.model_dump(exclude_unset=True)
    next_sku = str(values.get("sku") or product.sku).strip()
    duplicate = db.scalar(select(Product).where(func.lower(Product.sku) == next_sku.lower(), Product.id != product.id))
    if duplicate:
        raise HTTPException(409, "Артикул уже существует")
    _product_values(product, values)
    capability = _save_product_capability(db, product, values)
    db.add(AuditEvent(username=user.username, action="product_updated", entity_type="product", entity_id=str(product.id), details=payload.model_dump(mode="json", exclude_unset=True)))
    db.commit()
    return {"ok": True, "product_id": product.id, "capability_id": capability.id if capability else None}


@router.patch("/products/{product_id}/status")
def update_product_status(
    product_id: int, payload: ProductStatusUpdate, db: Session = Depends(get_db), user: UserContext = Depends(require_planner),
) -> dict:
    product = db.get(Product, product_id)
    if not product or product.catalog_status == "deleted":
        raise HTTPException(404, "Артикул не найден")
    status = payload.status.strip().lower()
    if status not in {"active", "blocked"}:
        raise HTTPException(422, "Допустимые состояния: active или blocked")
    product.catalog_status = status
    product.active = status == "active"
    if status == "blocked":
        product.fk_status = "Блокирован"
    elif (product.fk_status or "").lower() == "блокирован":
        product.fk_status = "Активный"
    db.add(AuditEvent(username=user.username, action=f"product_{status}", entity_type="product", entity_id=str(product.id), details={"sku": product.sku}))
    db.commit()
    return {"ok": True, "status": status}


@router.delete("/products/{product_id}")
def delete_product(
    product_id: int, db: Session = Depends(get_db), user: UserContext = Depends(require_planner),
) -> dict:
    product = db.get(Product, product_id)
    if not product or product.catalog_status == "deleted":
        raise HTTPException(404, "Артикул не найден")
    product.catalog_status = "deleted"
    product.active = False
    db.add(AuditEvent(username=user.username, action="product_deleted", entity_type="product", entity_id=str(product.id), details={"sku": product.sku, "mode": "soft_delete"}))
    db.commit()
    return {"ok": True, "deleted": True}


def _scalar(value):
    return value if isinstance(value, (str, int, float, bool)) or value is None else str(value)


def _bom_table(payload) -> tuple[list[str], list[dict]]:
    candidates: list[list[dict]] = []

    def collect(value) -> None:
        if isinstance(value, list):
            rows = [item for item in value if isinstance(item, dict)]
            if rows:
                candidates.append(rows)
            for item in value:
                collect(item)
        elif isinstance(value, dict):
            for item in value.values():
                collect(item)

    collect(payload)
    rows = max(candidates, key=len) if candidates else ([payload] if isinstance(payload, dict) else [{"Значение": payload}])
    columns: list[str] = []
    for row in rows:
        for key, value in row.items():
            if key not in columns and not isinstance(value, (dict, list)):
                columns.append(str(key))
    columns = columns[:16]
    return columns, [{column: _scalar(row.get(column)) for column in columns} for row in rows]


@router.get("/products/{product_id}/bom")
def product_bom(product_id: int, db: Session = Depends(get_db), user: UserContext = Depends(current_user)) -> dict:
    product = db.get(Product, product_id)
    if not product or product.catalog_status == "deleted":
        raise HTTPException(404, "Артикул не найден")
    url = f"{settings.bom_api_base_url.rstrip('/')}/{quote(product.sku, safe='')}"
    try:
        with httpx.Client(timeout=settings.bom_timeout_seconds, follow_redirects=True) as client:
            response = client.get(url, headers={"Accept": "application/json"})
            response.raise_for_status()
            payload = response.json()
    except httpx.TimeoutException as exc:
        raise HTTPException(504, "Сервис спецификаций не ответил вовремя. Попробуйте ещё раз из корпоративной сети.") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(502, "Не удалось получить спецификацию из BOM. Проверьте подключение к корпоративной сети.") from exc
    columns, rows = _bom_table(payload)
    return {"sku": product.sku, "product_name": product.name, "basis_units": settings.bom_basis_units, "source_url": url, "columns": columns, "rows": rows}


@router.get("/export.xlsx")
def download_catalog(db: Session = Depends(get_db), user: UserContext = Depends(require_planner)) -> Response:
    content = export_catalog(db)
    return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": 'attachment; filename="plan-portal-catalog.xlsx"'})


@router.post("/import.xlsx")
async def upload_catalog(
    file: UploadFile = File(...), db: Session = Depends(get_db), user: UserContext = Depends(require_planner),
) -> dict:
    if Path(file.filename or "").suffix.lower() != ".xlsx":
        raise HTTPException(422, "Загрузите файл справочника в формате XLSX")
    try:
        result = import_catalog(db, await file.read())
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    db.add(AuditEvent(username=user.username, action="catalog_imported", entity_type="catalog", details={**result, "file_name": file.filename}))
    db.commit()
    return {"ok": True, **result}


@router.patch("/capabilities/{capability_id}")
def update_capability(
    capability_id: int, payload: CapabilityUpdate, db: Session = Depends(get_db),
    user: UserContext = Depends(require_planner),
) -> dict:
    capability = db.scalar(select(LineCapability).where(LineCapability.id == capability_id).options(
        joinedload(LineCapability.product), joinedload(LineCapability.line),
    ))
    if not capability:
        raise HTTPException(404, "Строка справочника не найдена")
    values = payload.model_dump(exclude_unset=True)
    if "units_per_hour" in values and values["units_per_hour"] is None:
        raise HTTPException(422, "Скорость должна быть положительным числом")
    if "line_id" in values:
        line = db.get(ProductionLine, values.pop("line_id"))
        if not line or line.status != "active":
            raise HTTPException(422, "Выберите существующую производственную линию")
        duplicate = db.scalar(select(LineCapability).where(LineCapability.line_id == line.id, LineCapability.product_id == capability.product_id, LineCapability.id != capability.id))
        if duplicate:
            raise HTTPException(409, "Связь этого артикула и линии уже существует")
        capability.line = line
    if "line_status" in values:
        capability.technological_constraints = {**(capability.technological_constraints or {}), "source_line_status": values.pop("line_status")}
    if "advance_status" in values and values["advance_status"] not in {"АЗ", "По графику"}:
        raise HTTPException(422, "Статус должен быть АЗ или По графику")
    for field in ("advance_status", "fk_status", "unit_weight_kg", "box_weight_kg", "units_per_box", "state", "category", "product_name"):
        if field in values:
            setattr(capability.product, "name" if field == "product_name" else field, values.pop(field))
    if "mono_group" in values:
        capability.product.mono_group = (values.pop("mono_group") or "").strip() or None
    for field, value in values.items():
        setattr(capability, field, value)
    capability.line.default_capacity = Decimal(capability.units_per_hour) * Decimal(capability.line.working_hours)
    db.add(AuditEvent(
        username=user.username, action="capability_updated", entity_type="line_capability",
        entity_id=str(capability.id), details=payload.model_dump(mode="json", exclude_unset=True),
    ))
    db.flush()
    from app.services.plan_service import PlanService
    from app.services.line_schedule_service import ensure_line_capacities
    service = PlanService(db)
    plan = service.active_plan()
    if plan:
        demands = service._latest_source_demands()
        if demands:
            ensure_line_capacities(
                db,
                list(db.scalars(select(ProductionLine).where(ProductionLine.status == "active"))),
                plan.horizon_start,
                plan.horizon_end,
            )
            service.calculate(plan, demands, "catalog_updated")
    db.commit()
    return {"ok": True, "capability_id": capability.id}


class CapabilityCreate(BaseModel):
    line_id: int = Field(gt=0)
    units_per_hour: Decimal = Field(gt=0)


@router.post("/products/{product_id}/capabilities")
def assign_product(product_id: int, payload: CapabilityCreate, db: Session = Depends(get_db), user: UserContext = Depends(require_planner)):
    line = db.get(ProductionLine, payload.line_id)
    product = db.get(Product, product_id)
    if not product or not product.active or not line or line.status != "active":
        raise HTTPException(422, "Выберите существующие артикул и линию")
    if db.scalar(select(LineCapability).where(LineCapability.line_id == payload.line_id, LineCapability.product_id == product_id)):
        raise HTTPException(409, "Связь артикула и линии уже существует")
    capability = LineCapability(product_id=product_id, line_id=payload.line_id, units_per_hour=payload.units_per_hour)
    db.add(capability); db.flush()
    return update_capability(capability.id, CapabilityUpdate(units_per_hour=payload.units_per_hour), db, user)
