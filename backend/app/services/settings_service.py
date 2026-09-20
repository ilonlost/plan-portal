from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import PortalSetting


MAIL_SETTING_KEY = "mail_configuration"
PORTAL_SETTING_KEY = "portal_configuration"


def default_portal_configuration() -> dict:
    return {
        "section_visibility": {
            "plan": True, "catalog": True, "import": True, "az": True,
            "sources": True, "feedback": True, "fact": True,
        },
        "partial_downtime_percent": 50,
        "full_downtime_percent": 80,
    }


def get_portal_configuration(db: Session) -> dict:
    row = db.scalar(select(PortalSetting).where(PortalSetting.key == PORTAL_SETTING_KEY))
    saved = row.value if row else {}
    defaults = default_portal_configuration()
    return {
        **defaults,
        **saved,
        "section_visibility": {**defaults["section_visibility"], **saved.get("section_visibility", {})},
    }


def save_portal_configuration(db: Session, values: dict, username: str) -> dict:
    clean = get_portal_configuration(db)
    clean.update({key: value for key, value in values.items() if key in {"section_visibility", "partial_downtime_percent", "full_downtime_percent"}})
    clean["section_visibility"] = {**default_portal_configuration()["section_visibility"], **clean.get("section_visibility", {})}
    row = db.scalar(select(PortalSetting).where(PortalSetting.key == PORTAL_SETTING_KEY))
    if not row:
        row = PortalSetting(key=PORTAL_SETTING_KEY, value=clean, updated_by=username)
        db.add(row)
    else:
        row.value = clean
        row.updated_by = username
    db.flush()
    return clean


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
