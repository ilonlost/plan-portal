from __future__ import annotations

import smtplib
import ssl
from collections import defaultdict
from datetime import date
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from html import escape

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import NotificationLog
from app.services.settings_service import get_mail_configuration


def _recipients(configuration: dict, extra: list[str] | None = None) -> list[str]:
    values = [value.strip().lower() for value in str(configuration.get("notification_emails", "")).split(",") if value.strip()]
    values.extend(value.strip().lower() for value in (extra or []) if value and value.strip())
    return list(dict.fromkeys(value for value in values if "@" in value))


def _date(value: object) -> str:
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    text = str(value or "")
    try:
        return date.fromisoformat(text[:10]).strftime("%d.%m.%Y")
    except ValueError:
        return text or "—"


def _duration(value: object) -> str:
    minutes = round(float(value or 0) * 60)
    hours, remainder = divmod(minutes, 60)
    if hours and remainder:
        return f"{hours} ч {remainder} мин"
    if hours:
        return f"{hours} ч"
    return f"{remainder} мин"


def build_plan_email_html(
    configuration: dict,
    plan_name: str,
    start: date,
    end: date,
    items: list[dict],
) -> str:
    accent = str(configuration.get("accent_color") or "#c8102e")
    grouped: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for item in items:
        grouped[_date(item.get("production_date"))][str(item.get("line_name") or "Без линии")].append(item)
    sections: list[str] = []
    for day, lines in grouped.items():
        sections.append(f'<tr><td style="padding:18px 24px 8px;font-size:18px;font-weight:700;color:#202124">{escape(day)}</td></tr>')
        for line_name, rows in lines.items():
            body = "".join(
                '<tr>'
                f'<td style="padding:8px;border-bottom:1px solid #eceef1;font-weight:700">№{int(row.get("sequence") or 0)}</td>'
                f'<td style="padding:8px;border-bottom:1px solid #eceef1;font-weight:700">{escape(str(row.get("sku") or "—"))}</td>'
                f'<td style="padding:8px;border-bottom:1px solid #eceef1">{escape(str(row.get("product_name") or "—"))}</td>'
                f'<td style="padding:8px;border-bottom:1px solid #eceef1;white-space:nowrap">{escape("День" if row.get("shift") == "day" else "Ночь")}</td>'
                f'<td style="padding:8px;border-bottom:1px solid #eceef1;text-align:right;white-space:nowrap">{float(row.get("quantity_units") or 0):,.0f} шт.</td>'
                f'<td style="padding:8px;border-bottom:1px solid #eceef1;text-align:right;white-space:nowrap">{float(row.get("quantity_kg") or 0):,.2f} кг</td>'
                f'<td style="padding:8px;border-bottom:1px solid #eceef1;text-align:right;white-space:nowrap">{escape(_duration(row.get("required_hours")))}</td>'
                f'<td style="padding:8px;border-bottom:1px solid #eceef1;white-space:nowrap">{escape("МОЙКА" if row.get("schedule_kind") == "cleaning" else str(row.get("source_kind") or "").upper())}</td>'
                '</tr>' for row in rows
            )
            sections.append(
                f'<tr><td style="padding:8px 24px 18px"><div style="border:1px solid #dfe2e6;border-left:4px solid {escape(accent)}">'
                f'<div style="padding:10px 12px;background:#f4f5f7;font-weight:700;color:#202124">{escape(line_name)} · {len(rows)} поз.</div>'
                '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="border-collapse:collapse;font-size:12px;color:#34373c">'
                '<tr style="background:#fafafa;color:#6f747c"><th align="left" style="padding:7px">№</th><th align="left">SKU</th><th align="left">Готовая продукция</th><th align="left">Смена</th><th align="right">Штуки</th><th align="right">Кг</th><th align="right">Время</th><th align="left">Источник</th></tr>'
                f'{body}</table></div></td></tr>'
            )
    intro = escape(str(configuration.get("plan_intro") or "")).replace("\n", "<br>")
    footer = escape(str(configuration.get("plan_footer") or ""))
    button = escape(str(configuration.get("button_label") or "Открыть PLAN PORTAL"))
    total_kg = sum(float(item.get("quantity_kg") or 0) for item in items)
    total_units = sum(float(item.get("quantity_units") or 0) for item in items)
    sections_html = "".join(sections) or '<tr><td style="padding:24px">В выбранном периоде нет заданий.</td></tr>'
    return (
        '<!doctype html><html><body style="margin:0;background:#f1f3f5;font-family:Arial,sans-serif;color:#202124">'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0"><tr><td align="center" style="padding:24px 12px">'
        '<table role="presentation" width="760" cellspacing="0" cellpadding="0" style="width:100%;max-width:760px;background:#fff;border:1px solid #dfe2e6">'
        f'<tr><td style="padding:20px 24px;background:{escape(accent)};color:#fff"><div style="font-size:22px;font-weight:800">PLAN PORTAL</div><div style="margin-top:4px;font-size:12px;opacity:.88">{escape(plan_name)}</div></td></tr>'
        f'<tr><td style="padding:22px 24px 8px"><h1 style="margin:0 0 10px;font-size:22px">План производства · {_date(start)} — {_date(end)}</h1><div style="font-size:14px;line-height:1.6;color:#4f5359">{intro}</div>'
        f'<div style="margin-top:16px;padding:12px;background:#f4f5f7;border-left:4px solid {escape(accent)}"><b>{len(items)} позиций</b> · {total_units:,.0f} шт. · {total_kg:,.2f} кг · мощность линии 22 ч/сутки</div></td></tr>'
        f'{sections_html}'
        f'<tr><td style="padding:18px 24px"><a href="{escape(settings.app_url)}" style="display:inline-block;padding:11px 18px;background:{escape(accent)};color:#fff;text-decoration:none;font-weight:700">{button}</a></td></tr>'
        f'<tr><td style="padding:15px 24px;background:#f4f5f7;color:#777b82;font-size:11px">{footer}</td></tr>'
        '</table></td></tr></table></body></html>'
    )


def send_notification(
    db: Session,
    event_type: str,
    subject: str,
    text: str,
    extra_recipients: list[str] | None = None,
    html: str | None = None,
) -> NotificationLog:
    configuration = get_mail_configuration(db)
    recipients = _recipients(configuration, extra_recipients)
    log = NotificationLog(event_type=event_type, recipients=recipients, subject=subject, status="pending")
    db.add(log)
    db.flush()
    if not configuration.get("enabled"):
        log.status = "skipped"; log.error = "Почтовые уведомления отключены в админке"; db.commit(); return log
    if not recipients:
        log.status = "skipped"; log.error = "В админке не указаны получатели"; db.commit(); return log

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = formataddr((str(configuration.get("smtp_from_name") or "PLAN PORTAL"), str(configuration.get("smtp_from") or settings.smtp_from)))
    message["To"] = ", ".join(recipients)
    if configuration.get("smtp_reply_to"): message["Reply-To"] = str(configuration["smtp_reply_to"])
    message_id = make_msgid(domain=str(configuration.get("smtp_from") or settings.smtp_from).split("@")[-1])
    message["Message-ID"] = message_id
    message.set_content(text)
    if html: message.add_alternative(html, subtype="html")
    try:
        timeout = max(1, settings.smtp_timeout_ms / 1000)
        context = ssl.create_default_context(cafile=settings.smtp_ca_file or None)
        if not settings.smtp_tls_validate: context.check_hostname = False; context.verify_mode = ssl.CERT_NONE
        host, port = str(configuration.get("smtp_host") or settings.smtp_host), int(configuration.get("smtp_port") or settings.smtp_port)
        secure = bool(configuration.get("smtp_secure"))
        client = smtplib.SMTP_SSL(host, port, timeout=timeout, context=context) if secure else smtplib.SMTP(host, port, timeout=timeout)
        with client:
            client.ehlo()
            if configuration.get("smtp_require_tls") and not secure: client.starttls(context=context); client.ehlo()
            if settings.smtp_username: client.login(settings.smtp_username, settings.smtp_password)
            client.send_message(message)
        log.status = "sent"; log.message_id = message_id
    except (OSError, smtplib.SMTPException) as exc:
        log.status = "failed"; log.error = str(exc)[:2000]
    db.commit()
    return log
