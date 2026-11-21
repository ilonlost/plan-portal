from unittest.mock import MagicMock

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.entities import User
from app.services import notification_service as mail
from app.services.mail_routing import INHERIT
from app.services.settings_service import save_mail_configuration
from test_audit_regressions import client, db, make_plan


@pytest.fixture
def smtp(db, monkeypatch):
    save_mail_configuration(db, {
        "enabled": True, "notification_emails": "default@example.com",
        "event_recipients": {"planning": "planner@example.com", "it": "it@example.com"},
    }, "test")
    db.commit()
    connection = MagicMock()
    transport = connection.return_value.__enter__.return_value
    transport.send_message.return_value = {}
    monkeypatch.setattr(mail, "smtp_connection", connection)
    return transport


def update(client, action, mode, recipients="", previous=INHERIT):
    return client.put(f"/admin/mail-actions/{action}", json={
        "rule": {"mode": mode, "recipients": recipients}, "previous": previous,
    })


def test_routing_replaces_all_inherited_addresses_and_restores_defaults(client, db, smtp, monkeypatch):
    monkeypatch.setattr(settings, "feedback_emails", "help@example.com")
    key = "feedback_created"

    def overview():
        return next(action for action in client.get("/admin/overview").json()["mail_actions"] if action["key"] == key)

    assert overview()["inherited"] == ["it@example.com", "help@example.com"]
    assert update(client, key, "custom", "bad-address").status_code == 422
    rule = update(client, key, "custom", "Owner@example.com; owner@example.com").json()["rule"]
    assert overview()["rule"] == {"mode": "custom", "recipients": "owner@example.com"}
    assert update(client, key, "off").status_code == 409
    assert mail.send_notification(db, key, "test", "body").recipients == ["owner@example.com"]
    assert str(smtp.send_message.call_args.args[0]["To"]) == "owner@example.com"
    assert mail.send_notification(db, "manual_task_created", "test", "body").recipients == ["planner@example.com"]
    off = update(client, key, "off", previous=rule).json()["rule"]
    smtp.reset_mock()
    log = mail.send_notification(db, key, "test", "body")
    assert log.status == "skipped" and log.recipients == [] and log.error
    smtp.send_message.assert_not_called()
    assert update(client, key, "inherit", previous=off).status_code == 200
    assert mail.send_notification(db, key, "test", "body").recipients == overview()["inherited"]
    assert update(client, "unknown_action", "off").status_code == 404
    db.scalar(select(User).where(User.username == "regular.admin")).role = "planner"
    db.commit()
    assert update(client, key, "off").status_code == 403


@pytest.mark.parametrize("audience", ["production", "warehouse", "materials"])
def test_plan_preview_and_delivery_follow_the_same_rule(client, db, smtp, audience):
    plan, _ = make_plan(db)
    key = f"plan_email_{audience}"
    rule = update(client, key, "custom", "team@example.com").json()["rule"]
    preview_url = f"/admin/mail-preview?audience={audience}"
    payload = {"audience": audience, "recipients": ["extra@example.com"]}
    preview = client.get(preview_url).json()
    assert preview["routing_mode"] == "custom" and preview["recipients"] == ["team@example.com"]
    sent = client.post(f"/plans/{plan.id}/email", json=payload).json()
    assert sent["status"] == "sent" and sent["recipients"] == preview["recipients"]
    message = smtp.send_message.call_args.args[0]
    assert str(message["To"]) == "team@example.com" and len(list(message.iter_attachments())) == 1
    assert update(client, key, "off", previous=rule).status_code == 200
    assert client.get(preview_url).json()["routing_mode"] == "off"
    smtp.reset_mock()
    skipped = client.post(f"/plans/{plan.id}/email", json=payload).json()
    assert skipped["status"] == "skipped" and skipped["recipients"] == []
    smtp.send_message.assert_not_called()
