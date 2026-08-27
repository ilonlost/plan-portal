from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import PortalSetting


MAIL_SETTING_KEY = "mail_configuration"


def default_mail_configuration() -> dict:
    return {
        "enabled": settings.email_enabled,
        "smtp_host": settings.smtp_host,
        "smtp_port": settings.smtp_port,
        "smtp_from": settings.smtp_from,
        "smtp_from_name": settings.smtp_from_name,
        "smtp_reply_to": settings.smtp_reply_to,
        "smtp_secure": settings.smtp_secure,
        "smtp_require_tls": settings.smtp_require_tls,
        "notification_emails": settings.notification_emails,
        "plan_subject": "План производства ФК · {start} — {end}",
        "plan_intro": "Коллеги, направляем согласованный производственный план ФК.",
        "plan_footer": "Автоматическое уведомление PLAN PORTAL · agrohold.ru",
        "accent_color": "#c8102e",
        "button_label": "Открыть PLAN PORTAL",
    }


def get_mail_configuration(db: Session) -> dict:
    row = db.scalar(select(PortalSetting).where(PortalSetting.key == MAIL_SETTING_KEY))
    return {**default_mail_configuration(), **(row.value if row else {})}


def save_mail_configuration(db: Session, values: dict, username: str) -> dict:
    clean = {**default_mail_configuration(), **values}
    row = db.scalar(select(PortalSetting).where(PortalSetting.key == MAIL_SETTING_KEY))
    if not row:
        row = PortalSetting(key=MAIL_SETTING_KEY, value=clean, updated_by=username)
        db.add(row)
    else:
        row.value = clean
        row.updated_by = username
    db.flush()
    return clean
