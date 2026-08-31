from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.db.session import get_db
from app.core.security import UserContext, current_user, require_planner
from app.models.entities import AuditEvent, DemandItem, LineCapacity, LineCapability, LineScheduleTemplate, ProductionLine, ProductionPlan, ProductionScheduleItem
from app.services.line_schedule_service import DEFAULT_ANCHOR, SCHEDULE_LABELS, ensure_line_capacities, shift_hours
from app.services.plan_service import PlanService

router = APIRouter(prefix="/lines", tags=["lines"])


class CapacityDayUpdate(BaseModel):
    capacity_date: date
    day_hours: Decimal = Field(ge=0, le=11)
    night_hours: Decimal = Field(ge=0, le=11)
    note: str | None = Field(default=None, max_length=300)


class LineScheduleUpdate(BaseModel):
    schedule_code: str
    anchor_date: date
    template_id: int | None = None
    mail_recipients: str | None = Field(default=None, max_length=4000)
    csb_line_code: str | None = Field(default=None, max_length=40)
    csb_t5: str | None = Field(default=None, max_length=20)
    csb_t55: str | None = Field(default=None, max_length=80)
    slots: list[CapacityDayUpdate] = Field(default_factory=list)


class ScheduleTemplateCreate(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    description: str | None = Field(default=None, max_length=500)
    pattern: list[dict] = Field(min_length=1, max_length=31)


def _template_dict(template: LineScheduleTemplate) -> dict:
    return {"id": template.id, "name": template.name, "description": template.description, "pattern": template.pattern or []}


def _valid_pattern(pattern: list[dict]) -> list[dict]:
    clean: list[dict] = []
    for index, slot in enumerate(pattern, start=1):
        try:
            day_hours, night_hours = Decimal(str(slot.get("day_hours", 0))), Decimal(str(slot.get("night_hours", 0)))
        except Exception as exc:
            raise ValueError(f"День {index}: часы должны быть числом") from exc
        if not (Decimal("0") <= day_hours <= Decimal("11") and Decimal("0") <= night_hours <= Decimal("11")):
            raise ValueError(f"День {index}: в смене допустимо от 0 до 11 часов")
        clean.append({"day_hours": float(day_hours), "night_hours": float(night_hours)})
    return clean


@router.get("/schedule-templates")
def list_schedule_templates(db: Session = Depends(get_db), user: UserContext = Depends(current_user)) -> list[dict]:
    return [_template_dict(row) for row in db.scalars(select(LineScheduleTemplate).order_by(LineScheduleTemplate.name))]


@router.post("/schedule-templates")
def create_schedule_template(
    payload: ScheduleTemplateCreate, db: Session = Depends(get_db), user: UserContext = Depends(require_planner),
) -> dict:
    from fastapi import HTTPException
    if db.scalar(select(LineScheduleTemplate).where(LineScheduleTemplate.name == payload.name.strip())):
        raise HTTPException(422, "Шаблон с таким названием уже существует")
    try:
        pattern = _valid_pattern(payload.pattern)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    template = LineScheduleTemplate(name=payload.name.strip(), description=(payload.description or "").strip() or None, pattern=pattern, created_by=user.username)
    db.add(template)
    db.add(AuditEvent(username=user.username, action="line_schedule_template_created", entity_type="line_schedule_template", entity_id=None, details={"name": template.name, "days": len(pattern)}))
    db.commit()
    db.refresh(template)
    return _template_dict(template)


@router.get("")
def list_lines(db: Session = Depends(get_db), user: UserContext = Depends(current_user)) -> list[dict]:
    lines = list(db.scalars(select(ProductionLine).order_by(ProductionLine.priority, ProductionLine.code)))
    templates = {row.id: row for row in db.scalars(select(LineScheduleTemplate))}
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
            "schedule_code": line.schedule_code, "schedule_label": templates[line.schedule_template_id].name if line.schedule_template_id in templates else SCHEDULE_LABELS.get(line.schedule_code, line.schedule_code),
            "schedule_anchor_date": line.schedule_anchor_date,
            "schedule_template_id": line.schedule_template_id, "production_day_start_hour": line.production_day_start_hour,
            "mail_recipients": line.mail_recipients, "csb_line_code": line.csb_line_code, "csb_t5": line.csb_t5, "csb_t55": line.csb_t55,
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
            "available": row.available, "note": row.note, "manual_override": row.manual_override, "load_percent": float(load),
        })
    return result


@router.get("/{line_id}/schedule")
def line_schedule(
    line_id: int, start: date | None = None, days: int = 14,
    db: Session = Depends(get_db), user: UserContext = Depends(current_user),
) -> dict:
    line = db.get(ProductionLine, line_id)
    if not line:
        from fastapi import HTTPException
        raise HTTPException(404, "Линия не найдена")
    start = start or date.today()
    end = start + timedelta(days=max(1, min(days, 31)) - 1)
    rows = {(row.capacity_date, row.shift): row for row in db.scalars(select(LineCapacity).where(
        LineCapacity.line_id == line.id, LineCapacity.capacity_date >= start, LineCapacity.capacity_date <= end,
    ))}
    slots = []
    current = start
    while current <= end:
        default_day, default_night = shift_hours(line, current)
        day_row, night_row = rows.get((current, "day")), rows.get((current, "night"))
        slots.append({
            "capacity_date": current,
            "day_hours": Decimal(day_row.available_hours) if day_row and day_row.available else Decimal("0") if day_row else default_day,
            "night_hours": Decimal(night_row.available_hours) if night_row and night_row.available else Decimal("0") if night_row else default_night,
            "manual_override": bool((day_row and day_row.manual_override) or (night_row and night_row.manual_override)),
            "note": (day_row.note if day_row else None) or (night_row.note if night_row else None),
        })
        current += timedelta(days=1)
    return {
        "line_id": line.id, "line_name": line.name, "workshop_code": line.workshop_code,
        "schedule_code": line.schedule_code, "schedule_label": SCHEDULE_LABELS.get(line.schedule_code, line.schedule_code),
        "anchor_date": line.schedule_anchor_date or DEFAULT_ANCHOR, "patterns": SCHEDULE_LABELS,
        "templates": [_template_dict(row) for row in db.scalars(select(LineScheduleTemplate).order_by(LineScheduleTemplate.name))],
        "template_id": line.schedule_template_id, "production_day_start_hour": line.production_day_start_hour,
        "mail_recipients": line.mail_recipients or "", "csb_line_code": line.csb_line_code or "", "csb_t5": line.csb_t5 or "4", "csb_t55": line.csb_t55 or "",
        "slots": slots,
    }


@router.put("/{line_id}/schedule")
def update_line_schedule(
    line_id: int, payload: LineScheduleUpdate, db: Session = Depends(get_db),
    user: UserContext = Depends(require_planner),
) -> dict:
    from fastapi import HTTPException
    line = db.get(ProductionLine, line_id)
    if not line:
        raise HTTPException(404, "Линия не найдена")
    if payload.schedule_code not in SCHEDULE_LABELS:
        raise HTTPException(422, "Неизвестный шаблон графика")
    template = None
    if payload.template_id is not None:
        template = db.get(LineScheduleTemplate, payload.template_id)
        if not template:
            raise HTTPException(422, "Шаблон графика не найден")
        payload.schedule_code = "custom"
    if payload.schedule_code == "custom" and not template and not line.custom_schedule_pattern:
        raise HTTPException(422, "Для пользовательского графика выберите шаблон")
    next_pattern = _valid_pattern(template.pattern) if template else (line.custom_schedule_pattern or [])
    pattern_changed = (line.schedule_code != payload.schedule_code or line.schedule_anchor_date != payload.anchor_date
                       or line.schedule_template_id != payload.template_id or line.custom_schedule_pattern != next_pattern)
    line.schedule_code = payload.schedule_code
    line.schedule_anchor_date = payload.anchor_date
    line.schedule_template_id = payload.template_id
    line.custom_schedule_pattern = next_pattern if payload.schedule_code == "custom" else []
    line.mail_recipients = (payload.mail_recipients or "").strip() or None
    line.csb_line_code = (payload.csb_line_code or "").strip() or None
    line.csb_t5 = (payload.csb_t5 or "4").strip() or "4"
    line.csb_t55 = (payload.csb_t55 or "").strip() or None
    plan = db.scalar(select(ProductionPlan).where(ProductionPlan.active.is_(True)).order_by(ProductionPlan.updated_at.desc()))
    if plan and pattern_changed:
        for row in db.scalars(select(LineCapacity).where(
            LineCapacity.line_id == line.id,
            LineCapacity.capacity_date >= plan.horizon_start,
            LineCapacity.capacity_date <= plan.horizon_end,
        )):
            row.manual_override = False
        ensure_line_capacities(db, [line], plan.horizon_start, plan.horizon_end, refresh_generated=True)
    for slot in payload.slots:
        for shift, hours in (("day", slot.day_hours), ("night", slot.night_hours)):
            row = db.scalar(select(LineCapacity).where(
                LineCapacity.line_id == line.id, LineCapacity.capacity_date == slot.capacity_date, LineCapacity.shift == shift,
            ))
            if not row:
                row = LineCapacity(line_id=line.id, capacity_date=slot.capacity_date, shift=shift)
                db.add(row)
            row.available_hours = hours
            row.available = hours > 0
            row.manual_override = True
            row.note = slot.note or "Ручная корректировка графика"
    db.flush()
    recalculated = False
    if plan:
        demand_ids = list(db.scalars(select(ProductionScheduleItem.demand_item_id).where(
            ProductionScheduleItem.plan_id == plan.id, ProductionScheduleItem.demand_item_id.is_not(None),
        ).distinct()))
        demands = list(db.scalars(select(DemandItem).where(DemandItem.id.in_(demand_ids)).options(joinedload(DemandItem.product)))) if demand_ids else []
        if demands:
            PlanService(db).calculate(plan, demands, "line_schedule_changed")
            recalculated = True
        else:
            PlanService(db).recalculate_load(plan)
    db.add(AuditEvent(
        username=user.username, action="line_schedule_updated", entity_type="production_line", entity_id=str(line.id),
        details={"schedule_code": line.schedule_code, "template_id": line.schedule_template_id, "anchor_date": payload.anchor_date.isoformat(), "manual_days": len(payload.slots), "plan_recalculated": recalculated},
    ))
    db.commit()
    return {"ok": True, "line_id": line.id, "plan_recalculated": recalculated}
