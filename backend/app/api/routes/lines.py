from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.security import UserContext, current_user
from app.models.entities import LineCapacity, LineCapability, ProductionLine, ProductionScheduleItem

router = APIRouter(prefix="/lines", tags=["lines"])


@router.get("")
def list_lines(db: Session = Depends(get_db), user: UserContext = Depends(current_user)) -> list[dict]:
    lines = list(db.scalars(select(ProductionLine).order_by(ProductionLine.priority, ProductionLine.code)))
    result = []
    for line in lines:
        product_count = db.scalar(select(func.count(LineCapability.id)).where(LineCapability.line_id == line.id)) or 0
        today_load = db.scalar(select(func.max(ProductionScheduleItem.load_percent)).where(
            ProductionScheduleItem.line_id == line.id,
            ProductionScheduleItem.production_date >= date.today(),
        )) or 0
        result.append({
            "id": line.id, "code": line.code, "name": line.name, "status": line.status,
            "workshop_code": line.workshop_code, "workshop_name": line.workshop_name,
            "working_hours": line.working_hours, "default_capacity": line.default_capacity,
            "capacity_unit": line.capacity_unit, "priority": line.priority, "comments": line.comments,
            "product_count": product_count, "today_load": float(today_load),
        })
    return result


@router.get("/workshops")
def list_workshops(db: Session = Depends(get_db), user: UserContext = Depends(current_user)) -> list[dict]:
    lines = list(db.scalars(select(ProductionLine).order_by(ProductionLine.workshop_code, ProductionLine.priority, ProductionLine.name)))
    result: dict[str, dict] = {}
    for line in lines:
        workshop = result.setdefault(line.workshop_code, {"code": line.workshop_code, "name": line.workshop_name, "lines": []})
        workshop["lines"].append({"id": line.id, "code": line.code, "name": line.name, "status": line.status})
    return list(result.values())


@router.get("/capacities")
def list_capacities(start: date | None = None, days: int = 7, db: Session = Depends(get_db), user: UserContext = Depends(current_user)) -> list[dict]:
    start = start or date.today()
    end = start + timedelta(days=min(days, 31))
    rows = list(db.scalars(select(LineCapacity).where(
        LineCapacity.capacity_date >= start, LineCapacity.capacity_date < end,
    ).order_by(LineCapacity.capacity_date, LineCapacity.line_id)))
    line_names = {line.id: line.name for line in db.scalars(select(ProductionLine))}
    result = []
    for row in rows:
        load = db.scalar(select(func.max(ProductionScheduleItem.load_percent)).where(
            ProductionScheduleItem.line_id == row.line_id,
            ProductionScheduleItem.production_date == row.capacity_date,
            ProductionScheduleItem.shift == row.shift,
        )) or Decimal("0")
        result.append({
            "id": row.id, "line_id": row.line_id, "line_name": line_names[row.line_id],
            "capacity_date": row.capacity_date, "shift": row.shift,
            "available_hours": row.available_hours, "max_units": row.max_units,
            "available": row.available, "note": row.note, "load_percent": float(load),
        })
    return result
