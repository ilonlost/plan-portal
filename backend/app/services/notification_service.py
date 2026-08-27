from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import NotificationLog


def _recipients(extra: list[str] | None = None) -> list[str]:
    values = [value.strip() for value in settings.notification_emails.split(",") if value.strip()]
    values.extend(value.strip() for value in (extra or []) if value and value.strip())
    return list(dict.fromkeys(values))


def send_notification(
    db: Session,
    event_type: str,
    subject: str,
    text: str,
    extra_recipients: list[str] | None = None,
) -> NotificationLog:
    recipients = _recipients(extra_recipients)
    log = NotificationLog(event_type=event_type, recipients=recipients, subject=subject, status="pending")
    db.add(log)
    db.flush()

    if not settings.email_enabled:
        log.status = "skipped"
        log.error = "Почтовые уведомления отключены настройкой EMAIL_ENABLED"
        db.commit()
        return log
    if not recipients:
        log.status = "skipped"
        log.error = "Не указаны получатели NOTIFICATION_EMAILS"
        db.commit()
        return log

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((settings.smtp_from_name, settings.smtp_from))
    message["To"] = ", ".join(recipients)
    if settings.smtp_reply_to:
        message["Reply-To"] = settings.smtp_reply_to
    message_id = make_msgid(domain=settings.smtp_from.split("@")[-1] if "@" in settings.smtp_from else None)
    message["Message-ID"] = message_id
    message.set_content(text)

    try:
        timeout = max(1, settings.smtp_timeout_ms / 1000)
        context = ssl.create_default_context(cafile=settings.smtp_ca_file or None)
        if not settings.smtp_tls_validate:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        if settings.smtp_secure:
            client = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=timeout, context=context)
        else:
            client = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=timeout)
        with client:
            client.ehlo()
            if settings.smtp_require_tls and not settings.smtp_secure:
                client.starttls(context=context)
                client.ehlo()
            if settings.smtp_username:
                client.login(settings.smtp_username, settings.smtp_password)
            client.send_message(message)
        log.status = "sent"
        log.message_id = message_id
    except (OSError, smtplib.SMTPException) as exc:
        log.status = "failed"
        log.error = str(exc)[:2000]
    db.commit()
    return log
