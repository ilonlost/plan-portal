from datetime import date
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from sqlalchemy import create_engine, text

from app.core.config import settings
from app.core.security import check_section_access, user_sections
from app.models.entities import User
from app.services import notification_service as mail
from app.services.downtime_service import load_downtimes


def request(path):
    return Request({"type": "http", "method": "GET", "path": path, "headers": []})


def test_individual_sections_do_not_affect_other_users():
    alice = User(username="alice", role="viewer", section_permissions={"sources": False, "catalog": False})
    bob = User(username="bob", role="viewer", section_permissions={})
    with pytest.raises(HTTPException) as error:
        check_section_access(request("/api/catalog"), alice)
    assert error.value.status_code == 403
    check_section_access(request("/api/catalog"), bob)
    assert not user_sections(alice)["fact"]
    alice.section_permissions = {"fact": True}
    check_section_access(request("/api/production-fact"), alice)
    with pytest.raises(HTTPException):
        check_section_access(request("/api/production-fact"), bob)
    alice.role = "admin"
    assert all(user_sections(alice).values())


def test_denied_plan_covers_downloads_insights_and_comments():
    user = User(username="u", role="planner", section_permissions={"plan": False})
    for path in ("/api/plans/1/export.xlsx", "/api/lines/insights", "/api/lines/1/comments/2026-09-20", "/api/admin/mail-preview"):
        with pytest.raises(HTTPException):
            check_section_access(request(path), user)


def test_plain_smtp_does_not_require_ca_or_send_diagnostic_email(monkeypatch):
    client = MagicMock()
    client.__enter__.return_value = client
    client.ehlo.return_value = (250, b"ok")
    client.noop.return_value = (250, b"ok")
    monkeypatch.setattr(mail.smtplib, "SMTP", MagicMock(return_value=client))
    context = MagicMock(side_effect=AssertionError("Plain SMTP must not load certificates"))
    monkeypatch.setattr(mail.ssl, "create_default_context", context)
    monkeypatch.setattr(settings, "smtp_ca_file", "/missing/agrohold.pem")
    monkeypatch.setattr(settings, "smtp_username", "")
    assert mail.smtp_diagnostics({"smtp_host": "smtp.example", "smtp_port": 25, "smtp_require_tls": False})["ok"]
    client.starttls.assert_not_called()
    client.send_message.assert_not_called()


def test_required_tls_is_never_silently_downgraded(monkeypatch):
    client = MagicMock()
    client.__enter__.return_value = client
    client.ehlo.return_value = (250, b"ok")
    client.starttls.side_effect = mail.smtplib.SMTPNotSupportedError("STARTTLS unavailable")
    monkeypatch.setattr(mail.smtplib, "SMTP", MagicMock(return_value=client))
    monkeypatch.setattr(settings, "smtp_ca_file", "")
    assert not mail.smtp_diagnostics({"smtp_require_tls": True})["ok"]
    client.send_message.assert_not_called()


def test_downtime_crossing_midnight_is_clipped_and_split(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'downtimes.db'}"
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE line_downtimes(line_code text,start_at text,end_at text,downtime_type text,reason text)"))
        connection.execute(text("INSERT INTO line_downtimes VALUES ('5810','2026-09-19 23:00:00','2026-09-21 01:00:00','full','Repair')"))
    engine.dispose()
    monkeypatch.setattr(settings, "downtime_database_url", url)
    monkeypatch.setattr(settings, "downtime_query", "")
    status, rows = load_downtimes(date(2026, 9, 20), date(2026, 9, 21))
    assert status == "connected"
    assert len(rows) == 2
    assert [(row["end_at"] - row["start_at"]).total_seconds() / 3600 for row in rows] == [24, 1]
    assert rows[0]["start_at"].date() == date(2026, 9, 20)
