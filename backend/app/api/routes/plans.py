from io import BytesIO

from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.db.session import get_db
from app.core.security import UserContext, current_user, ensure_master_line, require_planner
from app.models.entities import AuditEvent, LineCapacity, ProductionLine, ProductionPlan, ProductionScheduleItem
from app.schemas.common import ExecutionStatusUpdate, PlanApprovalRequest, ScheduleEventCreate, ScheduleItemUpdate
from app.services.export_service import ExcelExportService
from app.services.plan_service import PlanService, plan_dict, schedule_item_dict
from app.services.notification_service import send_notification
from app.services.notification_service import build_plan_email_html
from app.services.settings_service import get_mail_configuration

router = APIRouter(prefix="/plans", tags=["plans"])


class PlanEmailRequest(BaseModel):
    recipients: list[str] = Field(default_factory=list)
    start: date | None = None
    end: date | None = None


@router.get("/active")
def active_plan(db: Session = Depends(get_db), user: UserContext = Depends(current_user)) -> dict:
    plan = PlanService(db).active_plan()
    if not plan:
        raise HTTPException(404, "Активный план не найден")
    return plan_dict(db, plan, user.line_name if user.role == "master" else None)


@router.get("/active/matrix")
def active_plan_matrix(
    start: date | None = None, days: int = 21, workshop_code: str | None = None, line_id: int | None = None,
    db: Session = Depends(get_db), user: UserContext = Depends(current_user),
) -> dict:
    plan = PlanService(db).active_plan()
    if not plan:
        raise HTTPException(404, "Активный план не найден")
    start = start or plan.horizon_start
    end = min(plan.horizon_end, start + timedelta(days=max(1, min(days, 92)) - 1))
    lines_query = select(ProductionLine).order_by(ProductionLine.workshop_code, ProductionLine.priority, ProductionLine.name)
    if workshop_code:
        lines_query = lines_query.where(ProductionLine.workshop_code == workshop_code)
    if line_id:
        lines_query = lines_query.where(ProductionLine.id == line_id)
    if user.role == "master":
        lines_query = lines_query.where(ProductionLine.name == user.line_name)
    lines = list(db.scalars(lines_query))
    line_ids = [line.id for line in lines]
    items = list(db.scalars(select(ProductionScheduleItem).where(
        ProductionScheduleItem.plan_id == plan.id,
        ProductionScheduleItem.line_id.in_(line_ids),
        ProductionScheduleItem.production_date >= start,
        ProductionScheduleItem.production_date <= end,
        ProductionScheduleItem.excluded.is_(False),
    ).options(joinedload(ProductionScheduleItem.product), joinedload(ProductionScheduleItem.line), joinedload(ProductionScheduleItem.demand_item)))) if line_ids else []
    capacities = list(db.scalars(select(LineCapacity).where(
        LineCapacity.line_id.in_(line_ids), LineCapacity.capacity_date >= start, LineCapacity.capacity_date <= end,
    ))) if line_ids else []
    capacity_map: dict[tuple[int, date], Decimal] = {}
    for slot in capacities:
        if slot.available:
            capacity_map[(slot.line_id, slot.capacity_date)] = capacity_map.get((slot.line_id, slot.capacity_date), Decimal("0")) + Decimal(slot.available_hours)
    dates = [start + timedelta(days=offset) for offset in range((end - start).days + 1)]
    workshops: dict[str, dict] = {}
    for line in lines:
        workshop = workshops.setdefault(line.workshop_code, {"code": line.workshop_code, "name": line.workshop_name, "lines": []})
        line_items = [item for item in items if item.line_id == line.id]
        cells = []
        for day in dates:
            day_items = [item for item in line_items if item.production_date == day]
            hours = sum((Decimal(item.required_hours) for item in day_items), Decimal("0"))
            capacity = capacity_map.get((line.id, day), Decimal(line.working_hours))
            load = float(hours / capacity * 100) if capacity else (999.0 if hours else 0.0)
            cells.append({
                "date": day, "planned_hours": hours, "capacity_hours": capacity, "load_percent": round(load, 1),
                "gap_hours": max(Decimal("0"), capacity - hours),
                "ohl_kg": sum((Decimal(item.quantity_kg or 0) for item in day_items if item.source_kind == "ohl"), Decimal("0")),
                "zam_kg": sum((Decimal(item.quantity_kg or 0) for item in day_items if item.source_kind == "zam"), Decimal("0")),
                "items": [schedule_item_dict(item) for item in sorted(day_items, key=lambda value: (value.shift, value.sequence))],
            })
        workshop["lines"].append({"id": line.id, "code": line.code, "name": line.name, "cells": cells})
    return {"plan": {"id": plan.id, "name": plan.name, "status": plan.status.value, "version": plan_dict(db, plan)["version"]}, "dates": dates, "workshops": list(workshops.values())}


@router.patch("/{plan_id}/items/{item_id}")
def update_item(plan_id: int, item_id: int, payload: ScheduleItemUpdate, db: Session = Depends(get_db), user: UserContext = Depends(require_planner)) -> dict:
    item = db.scalar(select(ProductionScheduleItem).where(
        ProductionScheduleItem.id == item_id, ProductionScheduleItem.plan_id == plan_id,
    ).options(joinedload(ProductionScheduleItem.plan), joinedload(ProductionScheduleItem.product), joinedload(ProductionScheduleItem.line), joinedload(ProductionScheduleItem.demand_item)))
    if not item:
        raise HTTPException(404, "Задание не найдено")
    plan = PlanService(db).update_item(item, payload.model_dump(exclude_unset=True))
    db.add(AuditEvent(username=user.username, action="schedule_item_updated", entity_type="schedule_item", entity_id=str(item.id), details=payload.model_dump(mode="json", exclude_unset=True)))
    db.commit()
    return plan_dict(db, plan)


@router.post("/{plan_id}/events")
def create_event(plan_id: int, payload: ScheduleEventCreate, db: Session = Depends(get_db), user: UserContext = Depends(require_planner)) -> dict:
    plan = db.get(ProductionPlan, plan_id)
    if not plan:
        raise HTTPException(404, "План не найден")
    try:
        plan = PlanService(db).create_event(plan, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.add(AuditEvent(username=user.username, action="schedule_event_created", entity_type="production_plan", entity_id=str(plan.id), details=payload.model_dump(mode="json")))
    db.commit()
    return plan_dict(db, plan)


@router.delete("/{plan_id}/items/{item_id}")
def delete_item(plan_id: int, item_id: int, db: Session = Depends(get_db), user: UserContext = Depends(require_planner)) -> dict:
    item = db.scalar(select(ProductionScheduleItem).where(
        ProductionScheduleItem.id == item_id, ProductionScheduleItem.plan_id == plan_id,
    ).options(joinedload(ProductionScheduleItem.plan), joinedload(ProductionScheduleItem.product)))
    if not item:
        raise HTTPException(404, "Задание не найдено")
    deleted_id = item.id
    plan = PlanService(db).delete_item(item)
    db.add(AuditEvent(username=user.username, action="schedule_item_deleted", entity_type="schedule_item", entity_id=str(deleted_id), details={"plan_id": plan_id}))
    db.commit()
    return plan_dict(db, plan)


@router.post("/{plan_id}/approve")
def approve_plan(plan_id: int, payload: PlanApprovalRequest, db: Session = Depends(get_db), user: UserContext = Depends(require_planner)) -> dict:
    plan = db.get(ProductionPlan, plan_id)
    if not plan:
        raise HTTPException(404, "План не найден")
    try:
        plan = PlanService(db).approve(plan, payload.comment)
        db.add(AuditEvent(username=user.username, action="plan_approved", entity_type="production_plan", entity_id=str(plan.id), details={"comment": payload.comment}))
        db.commit()
        send_notification(db, "plan_approved", f"PLAN Portal: план утверждён", f"Пользователь {user.display_name} утвердил план «{plan.name}».\nКомментарий: {payload.comment or '—'}")
        return plan_dict(db, plan)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.patch("/{plan_id}/items/{item_id}/execution-status")
def update_execution_status(
    plan_id: int, item_id: int, payload: ExecutionStatusUpdate,
    db: Session = Depends(get_db), user: UserContext = Depends(current_user),
) -> dict:
    item = db.scalar(select(ProductionScheduleItem).where(
        ProductionScheduleItem.id == item_id, ProductionScheduleItem.plan_id == plan_id,
    ).options(joinedload(ProductionScheduleItem.plan), joinedload(ProductionScheduleItem.product), joinedload(ProductionScheduleItem.line)))
    if not item:
        raise HTTPException(404, "Задание не найдено")
    if not item.line:
        raise HTTPException(422, "У задания не указана линия")
    ensure_master_line(user, item.line.workshop_code, item.line.name)
    try:
        plan = PlanService(db).update_execution_status(item, payload.status, payload.note, user.username)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    db.add(AuditEvent(username=user.username, action="execution_status_updated", entity_type="schedule_item", entity_id=str(item.id), details={"status": payload.status, "note": payload.note}))
    db.commit()
    if payload.status in {"not_shipped", "partially_shipped"}:
        send_notification(
            db, "shipment_deviation", f"PLAN Portal: отклонение отгрузки · {item.product.name if item.product else 'задание'}",
            f"Линия: {item.line.name}.\nСтатус: {payload.status}.\nПричина: {payload.note}.\nСообщил: {user.display_name}.",
        )
    return plan_dict(db, plan, user.line_name if user.role == "master" else None)


@router.get("/{plan_id}/export.xlsx")
def export_plan(plan_id: int, db: Session = Depends(get_db), user: UserContext = Depends(current_user)) -> StreamingResponse:
    plan = db.get(ProductionPlan, plan_id)
    if not plan:
        raise HTTPException(404, "План не найден")
    items = list(db.scalars(select(ProductionScheduleItem).where(ProductionScheduleItem.plan_id == plan_id).options(
        joinedload(ProductionScheduleItem.product), joinedload(ProductionScheduleItem.line), joinedload(ProductionScheduleItem.demand_item),
    )))
    content = ExcelExportService().build([schedule_item_dict(item) for item in items])
    return StreamingResponse(
        BytesIO(content), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="production-plan-{plan_id}.xlsx"'},
    )


@router.post("/{plan_id}/email")
def email_plan(
    plan_id: int,
    payload: PlanEmailRequest,
    db: Session = Depends(get_db),
    user: UserContext = Depends(require_planner),
) -> dict:
    plan = db.get(ProductionPlan, plan_id)
    if not plan:
        raise HTTPException(404, "План не найден")
    start, end = payload.start or plan.horizon_start, payload.end or plan.horizon_end
    if end < start:
        raise HTTPException(422, "Дата окончания раньше даты начала")
    items = list(db.scalars(select(ProductionScheduleItem).where(
        ProductionScheduleItem.plan_id == plan.id,
        ProductionScheduleItem.production_date >= start,
        ProductionScheduleItem.production_date <= end,
        ProductionScheduleItem.excluded.is_(False),
    ).options(joinedload(ProductionScheduleItem.product), joinedload(ProductionScheduleItem.line), joinedload(ProductionScheduleItem.demand_item)).order_by(
        ProductionScheduleItem.production_date, ProductionScheduleItem.line_id, ProductionScheduleItem.shift, ProductionScheduleItem.sequence,
    )))
    configuration = get_mail_configuration(db)
    try:
        subject = str(configuration.get("plan_subject") or "План производства ФК · {start} — {end}").format(
            start=start.strftime("%d.%m.%Y"), end=end.strftime("%d.%m.%Y"), plan=plan.name,
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(422, "В шаблоне темы разрешены только {start}, {end} и {plan}") from exc
    html = build_plan_email_html(configuration, plan.name, start, end, [schedule_item_dict(item) for item in items])
    log = send_notification(
        db, "production_plan_email", subject,
        f"План «{plan.name}» за период {start.strftime('%d.%m.%Y')} — {end.strftime('%d.%m.%Y')}. Позиций: {len(items)}.",
        payload.recipients, html,
    )
    db.add(AuditEvent(username=user.username, action="production_plan_emailed", entity_type="production_plan", entity_id=str(plan.id), details={"start": start.isoformat(), "end": end.isoformat(), "item_count": len(items), "status": log.status, "recipients": log.recipients}))
    db.commit()
    return {"ok": True, "status": log.status, "recipients": log.recipients, "item_count": len(items), "error": log.error}
