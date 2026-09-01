from __future__ import annotations

import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import UserContext, current_user, require_admin
from app.db.session import get_db
from app.models.entities import AuditEvent, FeedbackEntry, FeedbackEvent
from app.services.notification_service import send_notification


router = APIRouter(prefix="/feedback", tags=["feedback"])

FEEDBACK_CATEGORIES = {"suggestion", "problem", "question", "other"}
FEEDBACK_STATUSES = {"new", "in_progress", "resolved", "closed"}
FEEDBACK_CATEGORY_LABELS = {
    "suggestion": "Предложение", "problem": "Проблема", "question": "Вопрос", "other": "Другое",
}
FEEDBACK_STATUS_LABELS = {
    "new": "Новое", "in_progress": "В работе", "resolved": "Решено", "closed": "Закрыто",
}


class FeedbackCreate(BaseModel):
    category: str = "suggestion"
    subject: str
    message: str


class FeedbackUpdate(BaseModel):
    status: str
    comment: str = ""


def _entry_dict(row: FeedbackEntry) -> dict:
    return {
        "id": row.id, "category": row.category, "subject": row.subject, "message": row.message,
        "author_username": row.author_username, "author_name": row.author_name, "author_email": row.author_email,
        "status": row.status, "it_comment": row.it_comment, "notification_status": row.notification_status,
        "notification_error": row.notification_error, "resolved_at": row.resolved_at, "resolved_by": row.resolved_by,
        "created_at": row.created_at, "updated_at": row.updated_at,
    }


def _event_dict(row: FeedbackEvent) -> dict:
    return {
        "id": row.id, "feedback_id": row.feedback_id, "action": row.action, "description": row.description,
        "actor_username": row.actor_username, "actor_name": row.actor_name, "created_at": row.created_at,
    }


def _extra_recipients() -> list[str]:
    return [value.strip() for value in re.split(r"[;,]", settings.feedback_emails) if value.strip()]


def _validate_create(payload: FeedbackCreate) -> tuple[str, str, str]:
    category = payload.category.strip().lower()
    subject = payload.subject.strip()
    message = payload.message.strip()
    if category not in FEEDBACK_CATEGORIES:
        raise HTTPException(422, "Выберите корректный тип обращения")
    if not 3 <= len(subject) <= 180:
        raise HTTPException(422, "Тема должна содержать от 3 до 180 символов")
    if not 10 <= len(message) <= 5000:
        raise HTTPException(422, "Описание должно содержать от 10 до 5000 символов")
    return category, subject, message


@router.get("")
def list_feedback(
    status: str = Query(default="", max_length=30),
    query: str = Query(default="", max_length=180),
    db: Session = Depends(get_db),
    user: UserContext = Depends(current_user),
) -> dict:
    is_admin = user.role == "admin"
    statement = select(FeedbackEntry)
    if not is_admin:
        statement = statement.where(func.lower(FeedbackEntry.author_username) == user.username.lower())
    if status in FEEDBACK_STATUSES:
        statement = statement.where(FeedbackEntry.status == status)
    clean_query = query.strip()
    if clean_query and is_admin:
        pattern = f"%{clean_query.lower()}%"
        statement = statement.where(or_(
            func.lower(FeedbackEntry.subject).like(pattern),
            func.lower(FeedbackEntry.message).like(pattern),
            func.lower(FeedbackEntry.author_name).like(pattern),
            func.lower(FeedbackEntry.author_username).like(pattern),
        ))
    entries = list(db.scalars(statement.order_by(FeedbackEntry.created_at.desc()).limit(500)))
    ids = [entry.id for entry in entries]
    events = list(db.scalars(select(FeedbackEvent).where(FeedbackEvent.feedback_id.in_(ids)).order_by(FeedbackEvent.created_at))) if ids else []
    return {"entries": [_entry_dict(entry) for entry in entries], "events": [_event_dict(event) for event in events], "can_manage": is_admin}


@router.post("")
def create_feedback(
    payload: FeedbackCreate,
    request: Request,
    db: Session = Depends(get_db),
    user: UserContext = Depends(current_user),
) -> dict:
    category, subject, message = _validate_create(payload)
    entry = FeedbackEntry(
        category=category, subject=subject, message=message,
        author_username=user.username, author_name=user.display_name, author_email=user.email or None,
        ip_address=request.client.host if request.client else None,
        user_agent=(request.headers.get("user-agent") or "")[:500] or None,
    )
    db.add(entry)
    db.flush()
    db.add(FeedbackEvent(
        feedback_id=entry.id, action="created", description="Обращение зарегистрировано",
        actor_username=user.username, actor_name=user.display_name,
    ))
    db.add(AuditEvent(
        username=user.username, action="feedback_created", entity_type="feedback", entity_id=str(entry.id),
        details={"category": category, "subject": subject},
    ))
    db.commit()

    notification_text = (
        f"Тип: {FEEDBACK_CATEGORY_LABELS[category]}\n"
        f"Автор: {user.display_name} ({user.username})\n"
        f"E-mail: {user.email or 'не указан'}\n"
        f"Обращение №{entry.id}\n\n{message}"
    )
    try:
        notification = send_notification(
            db, "feedback_created", f"PLAN Portal: обратная связь — {subject}", notification_text,
            extra_recipients=_extra_recipients(),
        )
        entry.notification_status = notification.status
        entry.notification_error = notification.error
        description = "Уведомление передано в почтовый контур" if notification.status == "sent" else (
            notification.error or "Почтовое уведомление не отправлено"
        )
        db.add(FeedbackEvent(
            feedback_id=entry.id, action=f"notification_{notification.status}", description=description[:2000],
            actor_username="system", actor_name="PLAN PORTAL",
        ))
    except Exception:
        entry.notification_status = "failed"
        entry.notification_error = "Не удалось передать уведомление в почтовый контур"
        db.add(FeedbackEvent(
            feedback_id=entry.id, action="notification_failed", description=entry.notification_error,
            actor_username="system", actor_name="PLAN PORTAL",
        ))
    db.commit()
    return {"ok": True, "entry": _entry_dict(entry)}


@router.patch("/{feedback_id}")
def update_feedback(
    feedback_id: int,
    payload: FeedbackUpdate,
    db: Session = Depends(get_db),
    user: UserContext = Depends(require_admin),
) -> dict:
    entry = db.get(FeedbackEntry, feedback_id)
    if not entry:
        raise HTTPException(404, "Обращение не найдено")
    status = payload.status.strip().lower()
    comment = payload.comment.strip()
    if status not in FEEDBACK_STATUSES:
        raise HTTPException(422, "Выберите корректный статус обращения")
    if len(comment) > 2000:
        raise HTTPException(422, "Комментарий ИТ не должен превышать 2000 символов")
    previous_status = entry.status
    entry.status = status
    entry.it_comment = comment or None
    if status in {"resolved", "closed"}:
        entry.resolved_at = entry.resolved_at or datetime.now(timezone.utc)
        entry.resolved_by = user.display_name
    else:
        entry.resolved_at = None
        entry.resolved_by = None
    description = (
        "Комментарий ИТ обновлён" if previous_status == status
        else f"Статус изменён: {FEEDBACK_STATUS_LABELS[previous_status]} → {FEEDBACK_STATUS_LABELS[status]}"
    )
    if comment:
        description = f"{description}. {comment}"
    db.add(FeedbackEvent(
        feedback_id=entry.id, action="updated", description=description,
        actor_username=user.username, actor_name=user.display_name,
    ))
    db.add(AuditEvent(
        username=user.username, action="feedback_updated", entity_type="feedback", entity_id=str(entry.id),
        details={"status": status, "comment": bool(comment)},
    ))
    db.commit()
    return {"ok": True, "entry": _entry_dict(entry)}
