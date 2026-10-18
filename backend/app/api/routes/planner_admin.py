"""Delegated planning administration; never exposes SMTP or elevated roles."""
from typing import Literal
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.api.routes import admin
from app.core.security import UserContext, require_planner, user_sections
from app.db.session import get_db
from app.models.entities import AuditEvent, ImportedOrder, ProductionLine, ProductionPlan, ProductionScheduleItem, User
from app.services.plan_service import PlanService
from app.services.settings_service import get_planner_configuration, save_planner_configuration

router = APIRouter(prefix="/planner-admin", tags=["planner administration"])


class AccessUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["planner", "viewer"]
    active: bool = True
    section_permissions: dict[str, bool] | None = None


class AccessCreate(AccessUpdate):
    username: str = Field(min_length=1, max_length=80)
    display_name: str = Field(default="", max_length=160)
    email: str = Field(default="", max_length=200)


class RecipientsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    production: str = Field(max_length=4000)
    warehouse: str = Field(max_length=4000)
    materials: str = Field(max_length=4000)


class DowntimeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    partial_percent: int = Field(ge=0, le=100, strict=True)
    full_percent: int = Field(ge=0, le=100, strict=True)


@router.get("")
def overview(db: Session = Depends(get_db), user: UserContext = Depends(require_planner)) -> dict:
    configuration = get_planner_configuration(db)
    plan = PlanService(db).active_plan()
    return {
        "active_plan": {"id": plan.id, "version": plan.revision, "start": plan.horizon_start, "end": plan.horizon_end} if plan else None,
        "users": [{"id": row.id, "username": row.username, "display_name": row.display_name,
                   "email": row.email, "role": row.role, "active": row.active,
                   "section_permissions": user_sections(row)}
                  for row in db.scalars(select(User).where(User.role.in_(["planner", "viewer"])).order_by(User.display_name))],
        "recipients": configuration["recipients"],
        "lines": [{"id": row.id, "name": row.name, "workshop_name": row.workshop_name,
                   **configuration["line_downtime"].get(str(row.id), configuration["default_downtime"])}
                  for row in db.scalars(select(ProductionLine).where(ProductionLine.status == "active").order_by(ProductionLine.workshop_code, ProductionLine.priority, ProductionLine.name))],
        "sources": [{"id": row.id, "file_name": row.source_name, "template_type": row.template_type,
                     "imported_at": row.imported_at, "valid_rows": row.valid_rows, "scope_line_id": row.scope_line_id}
                    for row in db.scalars(select(ImportedOrder).where(ImportedOrder.status != "deleted").order_by(ImportedOrder.imported_at.desc(), ImportedOrder.id.desc()))],
    }


@router.get("/line-tasks")
def line_tasks(line_id: int, start: date, end: date, db: Session = Depends(get_db), user: UserContext = Depends(require_planner)) -> dict:
    from app.services.line_import_service import active_line
    active_line(db, line_id)
    if end < start or (end - start).days > 92:
        raise HTTPException(422, "Выберите корректный диапазон не более 93 дней")
    plan = PlanService(db).active_plan()
    if not plan:
        raise HTTPException(404, "Активный план не найден")
    count = db.scalar(select(func.count()).select_from(ProductionScheduleItem).where(
        ProductionScheduleItem.plan_id == plan.id, ProductionScheduleItem.line_id == line_id,
        ProductionScheduleItem.schedule_kind == "production", ProductionScheduleItem.excluded.is_(False),
        ProductionScheduleItem.production_date.between(start, end),
    ))
    return {"plan_id": plan.id, "version": plan.revision, "count": count}


@router.get("/directory-users")
def directory_users(query: str = Query(min_length=2, max_length=120), user: UserContext = Depends(require_planner)) -> dict:
    return admin.directory_users(query=query, user=user)


@router.post("/users")
def create_user(payload: AccessCreate, db: Session = Depends(get_db), user: UserContext = Depends(require_planner)) -> dict:
    return admin.create_user_access(admin.UserAccessCreate(**payload.model_dump()), db, user)


@router.patch("/users/{user_id}")
def update_user(user_id: int, payload: AccessUpdate, db: Session = Depends(get_db), user: UserContext = Depends(require_planner)) -> dict:
    target = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if not target:
        raise HTTPException(404, "Пользователь не найден")
    if target.role not in {"planner", "viewer"}:
        raise HTTPException(403, "В этом разделе можно изменять только планеров и пользователей с правом просмотра")
    if target.username.casefold() == user.username.casefold() and (not payload.active or payload.role != "planner"):
        raise HTTPException(409, "Нельзя отключить себя или снять собственные права планера")
    return admin.update_user_access(user_id, admin.UserAccessUpdate(**payload.model_dump()), db, user)


@router.put("/recipients")
def save_recipients(payload: RecipientsUpdate, db: Session = Depends(get_db), user: UserContext = Depends(require_planner)) -> dict:
    import re
    recipients = {}
    for audience, raw in payload.model_dump().items():
        values = list(dict.fromkeys(value.lower() for value in re.split(r"[,;\s]+", raw.strip()) if value))
        if any(not re.fullmatch(r"[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+", value) for value in values):
            raise HTTPException(422, "Укажите корректные адреса получателей через запятую")
        recipients[audience] = ", ".join(values)
    save_planner_configuration(db, {"recipients": recipients}, user.username)
    db.add(AuditEvent(username=user.username, action="plan_recipients_updated", entity_type="portal_setting", details={"recipients": recipients}))
    db.commit()
    return {"ok": True}


@router.put("/lines/{line_id}/downtime")
def save_downtime(line_id: int, payload: DowntimeUpdate, db: Session = Depends(get_db), user: UserContext = Depends(require_planner)) -> dict:
    if not db.get(ProductionLine, line_id):
        raise HTTPException(404, "Линия не найдена")
    save_planner_configuration(db, {"line_downtime": {str(line_id): payload.model_dump()}}, user.username)
    db.add(AuditEvent(username=user.username, action="line_downtime_settings_updated", entity_type="production_line", entity_id=str(line_id), details=payload.model_dump()))
    db.commit()
    return {"ok": True}


@router.delete("/sources/{source_id}")
def delete_source(source_id: int, db: Session = Depends(get_db), user: UserContext = Depends(require_planner)) -> dict:
    # Lock in the same order as catalogue-triggered rebuilds.
    plan = db.scalar(select(ProductionPlan).where(ProductionPlan.active.is_(True)).order_by(ProductionPlan.updated_at.desc()).with_for_update())
    source = db.scalar(select(ImportedOrder).where(ImportedOrder.id == source_id).with_for_update())
    if not source or source.status == "deleted":
        raise HTTPException(404, "Источник не найден")
    source.status = "deleted"
    removed = 0
    if plan:
        demand_ids = {row.id for row in source.items}
        for item in list(plan.schedule_items):
            if item.demand_item_id in demand_ids:
                plan.schedule_items.remove(item)
                db.delete(item)
                removed += 1
    db.add(AuditEvent(username=user.username, action="source_deleted", entity_type="imported_order", entity_id=str(source_id), details={"file_name": source.source_name, "removed_tasks": removed}))
    db.flush()
    recalculate = plan is not None and (source.template_type in {"ohl_daily", "quarter_weekly"} or removed > 0)
    if recalculate:
        service = PlanService(db)
        demands = service._latest_source_demands()
        if source.scope_line_id:
            from app.services.line_import_service import demands_for_line
            demands = demands_for_line(db, plan, demands, source.scope_line_id)
        kind = {"ohl_daily": "ohl", "quarter_weekly": "zam"}.get(source.template_type) if source.scope_line_id else None
        service.calculate(plan, demands, "source_deleted", line_id=source.scope_line_id, source_kind=kind)
    db.commit()
    return {"ok": True, "removed_tasks": removed, "plan_recalculated": recalculate}
