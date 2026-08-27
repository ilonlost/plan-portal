from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.core.security import UserContext, current_user, require_planner
from app.db.session import get_db
from app.models.entities import AuditEvent, ImportedOrder, LineCapability, Product, ProductionLine


router = APIRouter(prefix="/catalog", tags=["catalog"])


class CapabilityUpdate(BaseModel):
    units_per_hour: Decimal | None = Field(default=None, gt=0)
    batch_quantum_kg: Decimal | None = Field(default=None, gt=0)
    min_order_kg: Decimal | None = Field(default=None, ge=0)
    restrictions: str | None = Field(default=None, max_length=1000)


@router.get("")
def catalog(
    workshop_code: str | None = None, line_id: int | None = None, search: str | None = None,
    limit: int = Query(default=750, ge=1, le=2000), db: Session = Depends(get_db),
    user: UserContext = Depends(current_user),
) -> dict:
    query = select(LineCapability).options(
        joinedload(LineCapability.product), joinedload(LineCapability.line),
    ).join(LineCapability.product).join(LineCapability.line)
    if workshop_code:
        query = query.where(ProductionLine.workshop_code == workshop_code)
    if line_id:
        query = query.where(ProductionLine.id == line_id)
    if search:
        pattern = f"%{search.strip()}%"
        query = query.where(or_(Product.sku.ilike(pattern), Product.name.ilike(pattern), ProductionLine.name.ilike(pattern)))
    capabilities = list(db.scalars(query.order_by(ProductionLine.workshop_code, ProductionLine.name, Product.name).limit(limit)))
    products_total = db.scalar(select(func.count(Product.id))) or 0
    sources = list(db.scalars(select(ImportedOrder).order_by(ImportedOrder.imported_at.desc()).limit(20)))
    return {
        "summary": {
            "products": products_total,
            "capabilities": db.scalar(select(func.count(LineCapability.id))) or 0,
            "lines": db.scalar(select(func.count(ProductionLine.id))) or 0,
            "with_recipes": db.scalar(select(func.count(Product.id)).where(Product.recipe_component_count > 0)) or 0,
        },
        "rows": [{
            "capability_id": item.id,
            "product_id": item.product.id, "sku": item.product.sku, "product_name": item.product.name,
            "state": item.product.state, "category": item.product.category,
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
        } for item in capabilities],
        "sources": [{
            "id": item.id, "file_name": item.source_name, "template_type": item.template_type,
            "status": item.status, "total_rows": item.total_rows, "valid_rows": item.valid_rows,
            "invalid_rows": item.invalid_rows, "imported_at": item.imported_at,
        } for item in sources],
    }


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
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(capability, field, value)
    capability.line.default_capacity = Decimal(capability.units_per_hour) * Decimal(capability.line.working_hours)
    db.add(AuditEvent(
        username=user.username, action="capability_updated", entity_type="line_capability",
        entity_id=str(capability.id), details=payload.model_dump(mode="json", exclude_unset=True),
    ))
    db.commit()
    return {"ok": True, "capability_id": capability.id}
