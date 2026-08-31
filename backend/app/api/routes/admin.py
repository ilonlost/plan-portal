from __future__ import annotations

from datetime import date
import re

from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, joinedload

from app.core.auth_service import ldap_health
from app.core.config import settings
from app.core.security import UserContext, require_admin, require_planner
from app.db.session import get_db
from app.models.entities import (
    AuditEvent, DemandItem, ExportFile, ImportedOrder, ImportFile, IntegrationRun,
    NotificationLog, Product, ProductionLine, ProductionPlan, ProductionScheduleItem, User,
)
from app.services.notification_service import build_plan_email_html, send_notification
from app.services.plan_service import schedule_item_dict
from app.services.settings_service import get_mail_configuration, save_mail_configuration


router = APIRouter(prefix="/admin", tags=["admin"])


class DeletePlanRequest(BaseModel):
    confirmation: str


class UserAccessUpdate(BaseModel):
    role: str
    line_id: int | None = None
    active: bool = True


class UserAccessCreate(UserAccessUpdate):
    username: str
    display_name: str = ""
    email: str = ""


class MailConfigurationUpdate(BaseModel):
    configuration: dict


def _audit_dict(row: AuditEvent) -> dict:
    return {
        "id": row.id, "username": row.username, "action": row.action,
        "entity_type": row.entity_type, "entity_id": row.entity_id,
        "details": row.details, "created_at": row.created_at,
    }


def _notification_dict(row: NotificationLog) -> dict:
    return {
        "id": row.id, "event_type": row.event_type, "recipients": row.recipients,
        "subject": row.subject, "status": row.status, "error": row.error,
        "created_at": row.created_at,
    }


def _integration_dict(row: IntegrationRun) -> dict:
    return {
        "id": row.id, "integration": row.integration, "operation": row.operation,
        "target_date": row.target_date, "status": row.status, "test_mode": row.test_mode,
        "item_count": row.item_count, "response": row.response,
        "created_by": row.created_by, "created_at": row.created_at,
    }


@router.get("/overview")
def overview(db: Session = Depends(get_db), user: UserContext = Depends(require_admin)) -> dict:
    active = db.scalar(select(ProductionPlan).where(ProductionPlan.active.is_(True)).order_by(ProductionPlan.updated_at.desc()))
    mail_configuration = get_mail_configuration(db)
    return {
        "counts": {
            "plans": db.scalar(select(func.count(ProductionPlan.id))) or 0,
            "schedule_items": db.scalar(select(func.count(ProductionScheduleItem.id))) or 0,
            "products": db.scalar(select(func.count(Product.id))) or 0,
            "lines": db.scalar(select(func.count(ProductionLine.id))) or 0,
        },
        "active_plan": ({"id": active.id, "name": active.name, "status": active.status.value} if active else None),
        "ldap": ldap_health(),
        "email": {
            "enabled": bool(mail_configuration.get("enabled")),
            "configured": bool(mail_configuration.get("smtp_host") and mail_configuration.get("smtp_from")),
            "host": mail_configuration.get("smtp_host"),
        },
        "mail_configuration": mail_configuration,
        "smtp_password_configured": bool(settings.smtp_password),
        "csb": {"test_mode": settings.csb_test_mode, "configured": bool(settings.csb_endpoint)},
        "users": [{
            "id": row.id, "username": row.username, "display_name": row.display_name,
            "email": row.email, "role": row.role, "workshop_code": row.workshop_code,
            "line_name": row.line_name, "active": row.active, "last_login_at": row.last_login_at,
        } for row in db.scalars(select(User).order_by(User.display_name))],
        "lines": [{
            "id": row.id, "workshop_code": row.workshop_code,
            "workshop_name": row.workshop_name, "name": row.name,
        } for row in db.scalars(select(ProductionLine).order_by(ProductionLine.workshop_code, ProductionLine.priority, ProductionLine.name))],
        "recent_audit": [_audit_dict(row) for row in db.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(15))],
        "recent_notifications": [_notification_dict(row) for row in db.scalars(select(NotificationLog).order_by(NotificationLog.created_at.desc()).limit(15))],
        "recent_integrations": [_integration_dict(row) for row in db.scalars(select(IntegrationRun).order_by(IntegrationRun.created_at.desc()).limit(15))],
    }


@router.put("/mail-configuration")
def update_mail_configuration(
    payload: MailConfigurationUpdate,
    db: Session = Depends(get_db),
    user: UserContext = Depends(require_admin),
) -> dict:
    allowed = {
        "enabled", "smtp_host", "smtp_port", "smtp_from", "smtp_from_name", "smtp_reply_to",
        "smtp_secure", "smtp_require_tls", "notification_emails", "plan_subject", "plan_intro",
        "plan_footer", "accent_color", "button_label",
    }
    values = {key: value for key, value in payload.configuration.items() if key in allowed}
    if not str(values.get("smtp_host") or "").strip():
        raise HTTPException(422, "Укажите SMTP-сервер")
    try:
        port = int(values.get("smtp_port") or 25)
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, "Порт SMTP должен быть числом") from exc
    if port < 1 or port > 65535:
        raise HTTPException(422, "Порт SMTP должен быть от 1 до 65535")
    values["smtp_port"] = port
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", str(values.get("accent_color") or "")):
        raise HTTPException(422, "Цвет письма должен быть в формате #c8102e")
    configuration = save_mail_configuration(db, values, user.username)
    db.add(AuditEvent(username=user.username, action="mail_configuration_updated", entity_type="portal_setting", entity_id="mail_configuration", details={"enabled": configuration["enabled"], "smtp_host": configuration["smtp_host"]}))
    db.commit()
    return {"ok": True, "configuration": configuration}


@router.get("/mail-preview")
def mail_preview(
    start: date | None = None,
    end: date | None = None,
    line_ids: list[int] = Query(default=[]),
    db: Session = Depends(get_db),
    user: UserContext = Depends(require_planner),
) -> dict:
    plan = db.scalar(select(ProductionPlan).where(ProductionPlan.active.is_(True)).order_by(ProductionPlan.updated_at.desc()))
    if not plan:
        raise HTTPException(404, "Активный план не найден")
    start = start or plan.horizon_start
    end = end or min(plan.horizon_end, start)
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
    if line_ids:
        items = [item for item in items if item.line_id in set(line_ids)]
    html = build_plan_email_html(get_mail_configuration(db), plan.name, start, end, [schedule_item_dict(item) for item in items])
    return {"html": html, "item_count": len(items), "start": start, "end": end}


@router.patch("/users/{user_id}")
def update_user_access(
    user_id: int,
    payload: UserAccessUpdate,
    db: Session = Depends(get_db),
    user: UserContext = Depends(require_admin),
) -> dict:
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(404, "Пользователь не найден")
    if payload.role not in {"admin", "planner", "master", "viewer"}:
        raise HTTPException(422, "Неизвестная роль")
    line = db.get(ProductionLine, payload.line_id) if payload.line_id else None
    if payload.role == "master" and not line:
        raise HTTPException(422, "Для мастера выберите производственную линию")
    target.role = payload.role
    target.active = payload.active
    target.workshop_code = line.workshop_code if line else None
    target.line_name = line.name if line else None
    details = {"target": target.username, "role": payload.role, "line": target.line_name, "active": target.active}
    db.add(AuditEvent(username=user.username, action="user_access_updated", entity_type="user", entity_id=str(target.id), details=details))
    db.commit()
    return {"ok": True, **details}


@router.post("/users")
def create_user_access(
    payload: UserAccessCreate,
    db: Session = Depends(get_db),
    user: UserContext = Depends(require_admin),
) -> dict:
    username = payload.username.strip()
    if not username:
        raise HTTPException(422, "Укажите корпоративный логин")
    if payload.role not in {"admin", "planner", "master", "viewer"}:
        raise HTTPException(422, "Неизвестная роль")
    line = db.get(ProductionLine, payload.line_id) if payload.line_id else None
    if payload.role == "master" and not line:
        raise HTTPException(422, "Для мастера выберите производственную линию")
    target = db.scalar(select(User).where(func.lower(User.username) == username.lower()))
    if target:
        raise HTTPException(409, "Пользователь уже добавлен")
    target = User(
        username=username,
        display_name=payload.display_name.strip() or username,
        email=payload.email.strip() or None,
        role=payload.role,
        active=payload.active,
        workshop_code=line.workshop_code if line else None,
        line_name=line.name if line else None,
        ldap_groups=[],
    )
    db.add(target)
    db.flush()
    details = {"target": target.username, "role": target.role, "line": target.line_name, "active": target.active}
    db.add(AuditEvent(username=user.username, action="user_access_created", entity_type="user", entity_id=str(target.id), details=details))
    db.commit()
    return {"ok": True, "id": target.id, **details}


@router.post("/delete-plan-data")
def delete_plan_data(
    payload: DeletePlanRequest,
    db: Session = Depends(get_db),
    user: UserContext = Depends(require_admin),
) -> dict:
    if payload.confirmation.strip() != "УДАЛИТЬ ПЛАН":
        raise HTTPException(422, "Для подтверждения введите: УДАЛИТЬ ПЛАН")

    plan_ids = list(db.scalars(select(ProductionPlan.id)))
    order_ids = list(db.scalars(select(ImportedOrder.id).where(
        ImportedOrder.template_type.in_(("ohl_daily", "quarter_weekly", "generic")),
    )))
    item_count = db.scalar(select(func.count(ProductionScheduleItem.id))) or 0

    if plan_ids:
        db.execute(delete(ExportFile).where(ExportFile.plan_id.in_(plan_ids)))
        db.execute(delete(ProductionPlan).where(ProductionPlan.id.in_(plan_ids)))
    if order_ids:
        db.execute(delete(ImportFile).where(ImportFile.imported_order_id.in_(order_ids)))
        db.execute(delete(ImportedOrder).where(ImportedOrder.id.in_(order_ids)))

    details = {"plans_deleted": len(plan_ids), "schedule_items_deleted": item_count, "demand_imports_deleted": len(order_ids)}
    db.add(AuditEvent(username=user.username, action="plan_data_deleted", entity_type="production_plan", details=details))
    db.commit()
    send_notification(
        db, "plan_data_deleted", "PLAN Portal: производственный план очищен",
        f"Пользователь {user.display_name} удалил все планы и файлы спроса.\n\n{details}\n\nСправочник продукции и мощностей сохранён.",
    )
    return {"ok": True, **details, "catalog_preserved": True}
