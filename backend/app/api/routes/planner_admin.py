"""Delegated planning administration; never exposes SMTP or elevated roles."""
from typing import Literal
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from sqlalchemy import select, func
from sqlalchemy.orm import Session, joinedload

from app.api.routes import admin
from app.core.security import UserContext, require_planner, user_sections
from app.db.session import get_db
from app.models.entities import AuditEvent, ImportedOrder, ProductionLine, ProductionPlan, ProductionScheduleItem, User
from app.services.plan_service import PlanService, schedule_item_dict
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


class PlacementUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    complete_ohl: bool = Field(strict=True)
    complete_zam: bool = Field(strict=True)
    plan_version: int | None = Field(default=None, ge=1)


class ServiceAccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(max_length=80)
    display_name: str = Field(max_length=160)
    password: SecretStr


class ServicePasswordUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: SecretStr


@router.post("/service-accounts")
def create_service(payload: ServiceAccountCreate, db: Session = Depends(get_db), user: UserContext = Depends(require_planner)):
    from app.services.service_account_service import create_service_account
    row = create_service_account(db, payload.username, payload.display_name, payload.password.get_secret_value())
    db.add(AuditEvent(username=user.username, action="service_account_created", entity_type="user", entity_id=str(row.id), details={"target": row.username, "role": "viewer"}))
    db.commit()
    return {"id": row.id, "username": row.username, "role": "viewer"}


@router.put("/service-accounts/{user_id}/password")
def reset_service_password(user_id: int, payload: ServicePasswordUpdate, db: Session = Depends(get_db), user: UserContext = Depends(require_planner)):
    from app.services.service_account_service import password_hash
    row = db.scalar(select(User).where(User.id == user_id).with_for_update())
    if not row or row.auth_provider != "service":
        raise HTTPException(404, "Сервисная учётная запись не найдена")
    try:
        row.password_hash = password_hash(payload.password.get_secret_value())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    row.session_version += 1
    db.add(AuditEvent(username=user.username, action="service_account_password_reset", entity_type="user", entity_id=str(row.id), details={"target": row.username}))
    db.commit()
    return {"ok": True}


@router.get("")
def overview(db: Session = Depends(get_db), user: UserContext = Depends(require_planner)) -> dict:
    configuration = get_planner_configuration(db)
    plan = PlanService(db).active_plan()
    return {
        "active_plan": {"id": plan.id, "version": plan.revision, "start": plan.horizon_start, "end": plan.horizon_end} if plan else None,
        "users": [{"id": row.id, "username": row.username, "display_name": row.display_name,
                   "email": row.email, "role": row.role, "active": row.active,
                   "auth_provider": row.auth_provider,
                   "section_permissions": user_sections(row)}
                  for row in db.scalars(select(User).where(User.role.in_(["planner", "viewer"])).order_by(User.display_name))],
        "recipients": configuration["recipients"],
        "placement": configuration["placement"],
        "lines": [{"id": row.id, "name": row.name, "workshop_name": row.workshop_name,
                   **configuration["line_downtime"].get(str(row.id), configuration["default_downtime"])}
                  for row in db.scalars(select(ProductionLine).where(ProductionLine.status == "active").order_by(ProductionLine.workshop_code, ProductionLine.priority, ProductionLine.name))],
        "sources": [{"id": row.id, "file_name": row.source_name, "template_type": row.template_type,
                     "imported_at": row.imported_at, "valid_rows": row.valid_rows, "total_rows": row.total_rows, "invalid_rows": row.invalid_rows,
                     "scope_line_id": row.scope_line_id, "scope_start": row.scope_start, "scope_end": row.scope_end}
                    for row in db.scalars(select(ImportedOrder).where(ImportedOrder.status != "deleted").order_by(ImportedOrder.imported_at.desc(), ImportedOrder.id.desc()))],
    }


@router.put("/placement")
def save_placement(payload: PlacementUpdate, db: Session = Depends(get_db), user: UserContext = Depends(require_planner)) -> dict:
    plan = db.scalar(select(ProductionPlan).where(ProductionPlan.active.is_(True)).order_by(ProductionPlan.updated_at.desc()).with_for_update())
    if plan and payload.plan_version != plan.revision:
        raise HTTPException(409, "План изменён. Обновите настройки и повторите сохранение.")
    values = payload.model_dump(exclude={"plan_version"})
    previous = get_planner_configuration(db)["placement"]
    changed = {key for key, value in values.items() if previous[key] != value}
    if not changed:
        return {"ok": True, "plan_recalculated": False}
    save_planner_configuration(db, {"placement": values}, user.username)
    db.add(AuditEvent(username=user.username, action="placement_rules_updated", entity_type="portal_setting", details=values))
    service = PlanService(db)
    demands = service._latest_source_demands() if plan else []
    if demands:
        service.calculate(plan, demands, "placement_rules_updated", source_kind="zam" if changed == {"complete_zam"} else None, update_all_loads=True)
    elif plan:
        service.recalculate_load(plan)
        service.create_version(plan, "placement_rules_updated", "Изменены правила полного размещения ОХЛ / ЗАМ")
    db.commit()
    return {"ok": True, "plan_recalculated": bool(plan)}


@router.get("/line-tasks")
def line_tasks(start: date, end: date, line_id: int | None = None, db: Session = Depends(get_db), user: UserContext = Depends(require_planner)) -> dict:
    from app.services.line_import_service import active_line
    if line_id is not None:
        active_line(db, line_id)
    if end < start or (end - start).days > 92:
        raise HTTPException(422, "Выберите корректный диапазон не более 93 дней")
    plan = PlanService(db).active_plan()
    if not plan:
        raise HTTPException(404, "Активный план не найден")
    rows = list(db.scalars(select(ProductionScheduleItem).where(
        ProductionScheduleItem.plan_id == plan.id,
        *([ProductionScheduleItem.line_id == line_id] if line_id is not None else []),
        ProductionScheduleItem.schedule_kind == "production", ProductionScheduleItem.excluded.is_(False),
        ProductionScheduleItem.production_date.between(start, end),
    ).options(joinedload(ProductionScheduleItem.product), joinedload(ProductionScheduleItem.line), joinedload(ProductionScheduleItem.demand_item))
        .order_by(ProductionScheduleItem.production_date, ProductionScheduleItem.line_id, ProductionScheduleItem.sequence)))
    return {"plan_id": plan.id, "version": plan.revision, "count": len(rows), "items": [schedule_item_dict(row) for row in rows]}


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
        service.calculate(plan, demands, "source_deleted", line_id=source.scope_line_id, source_kind=kind, preserve_other_lines=bool(source.scope_line_id), period_start=source.scope_start, period_end=source.scope_end)
    db.commit()
    return {"ok": True, "removed_tasks": removed, "plan_recalculated": recalculate}
