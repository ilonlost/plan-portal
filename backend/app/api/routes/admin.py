from __future__ import annotations

from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.auth_service import ldap_health
from app.core.config import settings
from app.core.security import UserContext, require_admin
from app.db.session import get_db
from app.models.entities import (
    AuditEvent, DemandItem, ExportFile, ImportedOrder, ImportFile, IntegrationRun,
    NotificationLog, Product, ProductionLine, ProductionPlan, ProductionScheduleItem, User,
)
from app.services.notification_service import send_notification


router = APIRouter(prefix="/admin", tags=["admin"])


class DeletePlanRequest(BaseModel):
    confirmation: str


class UserAccessUpdate(BaseModel):
    role: str
    line_id: int | None = None
    active: bool = True


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
            "enabled": settings.email_enabled,
            "configured": bool(settings.smtp_host and settings.smtp_from and settings.notification_emails),
            "host": settings.smtp_host,
        },
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
