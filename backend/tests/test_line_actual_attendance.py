from datetime import datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import select

from app.api.routes import admin, lines, session
from app.core.config import settings
from app.core.security import UserContext, create_session_token
from app.models.entities import AuditEvent, ProductionLine, User
from app.services.tail_buffer_service import MOSCOW
from test_audit_regressions import client, db


def test_master_submits_daily_headcount_and_mail_uses_account_name(client, db, monkeypatch):
    line = db.scalar(select(ProductionLine).where(ProductionLine.name == "Булка"))
    other = db.scalar(select(ProductionLine).where(ProductionLine.name == "Хлеба"))
    db.add(User(username="bread.master", display_name="Иванов Иван Иванович", role="master",
                workshop_code=line.workshop_code, line_name=line.name, section_permissions={"catalog": False}))
    db.commit()
    day = datetime.now(MOSCOW).date()
    url = f"/lines/{line.id}/actual-attendance/{day}"
    assert client.put(url, json={"headcount": 15}).status_code == 403

    client.cookies.set(settings.session_cookie_name, create_session_token(UserContext("bread.master", "Token name", "master")))
    mails = []

    def send(db, event_type, subject, body):
        mails.append((event_type, subject, body))
        return SimpleNamespace(status="sent" if len(mails) == 1 else "failed")

    monkeypatch.setattr(lines, "send_notification", send)
    assert client.put(f"/lines/{other.id}/actual-attendance/{day}", json={"headcount": 15}).status_code == 403
    assert client.put(f"/lines/{line.id}/actual-attendance/{day + timedelta(days=1)}", json={"headcount": 15}).status_code == 422
    for invalid in (-1, 2.5, True, "2"):
        assert client.put(url, json={"headcount": invalid}).status_code == 422

    saved = client.put(url, json={"headcount": 0, "author_name": "Подмена"}).json()
    assert saved["mail_status"] == "sent"
    assert saved["attendance"]["headcount"] == 0
    assert saved["attendance"]["author_name"] == "Иванов Иван Иванович"
    assert "Булка" in mails[0][1] and "Иванов Иван Иванович" in mails[0][2] and "0 чел." in mails[0][2]
    assert client.put(url, json={"headcount": 0}).json()["mail_status"] == "unchanged"
    assert len(mails) == 1

    changed = client.put(url, json={"headcount": 4}).json()
    assert changed["mail_status"] == "failed"
    assert client.get(f"/lines/insights?start={day}&end={day}").json()["actual_attendance"] == [changed["attendance"]]
    assert len(list(db.scalars(select(AuditEvent).where(AuditEvent.action == "line_actual_attendance_saved")))) == 2


def test_admin_assigns_master_and_art_sign_in_keeps_line(client, db, monkeypatch):
    line = db.scalar(select(ProductionLine).where(ProductionLine.name == "Булка"))
    other = db.scalar(select(ProductionLine).where(ProductionLine.name == "Хлеба"))
    target = User(username="bread.employee", display_name="Сотрудник", role="viewer")
    db.add(target)
    db.commit()
    grant = f"/admin/users/{target.id}"
    monkeypatch.setattr(admin, "send_notification", lambda *args: None)
    assert client.patch(grant, json={"role": "master"}).status_code == 422
    assert client.patch(grant, json={"role": "master", "line_id": 200000}).status_code == 422
    assert client.patch(grant, json={"role": "master", "line_id": line.id}).json()["line"] == line.name
    created = client.post("/admin/users", json={"username": "new.master", "role": "master", "line_id": other.id})
    assert created.status_code == 200
    assert db.get(User, created.json()["id"]).workshop_code == other.workshop_code

    monkeypatch.setattr(settings, "art_portal_sso_required", True)
    monkeypatch.setattr(settings, "art_portal_url", "https://art.example")
    monkeypatch.setattr(settings, "art_portal_integration_token", "local-test-key")
    monkeypatch.setattr(session, "parse_art_portal_handoff", lambda _: UserContext(target.username, "ФИО Мастера", "viewer"))
    signed_in = client.post("/session/art-portal", json={"handoff_token": "test"}).json()["user"]
    assert (signed_in["role"], signed_in["workshop_code"], signed_in["line_name"]) == ("master", line.workshop_code, line.name)
    assert client.patch(grant, json={"role": "viewer"}).status_code == 403

    target.active = False
    db.commit()
    assert client.post("/session/art-portal", json={"handoff_token": "test"}).status_code == 403
