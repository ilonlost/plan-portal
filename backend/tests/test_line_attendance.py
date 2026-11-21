from datetime import datetime, timedelta

from sqlalchemy import select

from app.api.routes import lines
from app.models.entities import ProductionLine
from app.services.line_attendance import count_by_line
from test_audit_regressions import client, db


def test_distinct_sanitary_entries_in_calendar_day():
    start = datetime(2026, 11, 18)
    end = start + timedelta(days=1)
    staff = [(code, "Линия булки. Смена №1") for code in "ABCDEF"]
    staff += [("a", "Линия булки. Смена №2"), ("G", "Линия супов. Смена №2")]
    staff += [("H", "Участок упаковки. Смена №1")]
    entry = {"Code": " A ", "RepDate": start, "TypePass": "ВХОД",
             "AreaName": "ФК-САНПРОПУСКНИК(ЛК3)", "TerminalName": "ЛК3-ВХОД"}
    events = [entry, entry, {**entry, "Code": "B", "TerminalName": "ЛК3-ВЫХОД"},
              {**entry, "Code": "C", "TypePass": "ВЫХОД"},
              {**entry, "Code": "D", "AreaName": "Вестибюль"},
              {**entry, "Code": "E", "RepDate": end},
              {**entry, "Code": "F", "RepDate": end - timedelta(seconds=1)},
              {**entry, "Code": "G", "RepDate": start - timedelta(seconds=1)},
              {**entry, "Code": "H"}]
    assert count_by_line(staff, events, start, end) == {"булка": 2, "супы": 0}


def test_tiles_only_include_assigned_lines(client, db, monkeypatch):
    monkeypatch.setattr(lines, "today_attendance", lambda: {
        "date": "2026-11-18", "status": "ok", "counts": {"булка": 0},
    })
    response = client.get("/lines/attendance/today")
    assert response.status_code == 200
    counts = response.json()["counts"]
    names = {row.name: counts[str(row.id)] for row in db.scalars(select(ProductionLine)) if str(row.id) in counts}
    assert len(names) == 12
    assert names["Булка"] == 0 and names["Лазанья"] is None
    assert names.keys().isdisjoint({"Ручная зона ПЦ", "Ручная зона КЦ", "НАЧИНКА", "Горячий цех"})
    client.cookies.clear()
    assert client.get("/lines/attendance/today").status_code == 401
