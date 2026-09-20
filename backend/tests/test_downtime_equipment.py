from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.core.config import settings
from app.services import downtime_service as service
from tests.test_audit_regressions import db
from app.api.routes import lines as line_routes
from app.core.security import UserContext
from app.models.entities import ProductionLine
from sqlalchemy import select


def test_equipment_names_match_without_using_unrelated_parenthesized_codes():
    lines = [SimpleNamespace(id=1, code="FK-KC-90", csb_line_code="5440", name="Лазанья", workshop_code="KC"),
             SimpleNamespace(id=2, code="FK-KC-70", csb_line_code="5480", name="Сэндвичи", workshop_code="KC")]
    assert service.resolve_downtime_line({"line_name": "Линия лазаньи (1166)", "workshop_name": "КУЛИНАРИЯ"}, lines).id == 1
    assert service.resolve_downtime_line({"line_name": "Линия сэндвичей (1117)"}, lines).id == 2
    assert service.resolve_downtime_line({"line_name": "Линия лазаньи (1166)", "workshop_name": "ПЕКАРНЯ"}, lines) is None
    assert service.resolve_downtime_line({"line_name": "Неизвестный цех"}, lines) is None
    lines.append(SimpleNamespace(id=3, code="other", csb_line_code="5440", name="Другая", workshop_code="KC"))
    assert service.resolve_downtime_line({"line_code": "5440"}, lines) is None


def test_equipment_query_reports_bad_intervals_without_losing_valid_rows(monkeypatch):
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.mappings.return_value.all.return_value = [
        {"id": 1, "line_name": "Линия лазаньи (1166)", "start_at": datetime(2026, 9, 20, 10), "end_at": datetime(2026, 9, 20, 9), "downtime_type": "full"},
        {"id": 2, "line_name": "Линия лазаньи (1166)", "start_at": datetime(2026, 9, 20, 10), "end_at": datetime(2026, 9, 20, 11), "downtime_type": None},
        {"id": 3, "start_at": "bad date", "end_at": None},
        {"id": 4, "start_at": None, "end_at": datetime(2026, 9, 20, 12)},
        {"id": 5, "start_at": datetime(2026, 9, 20, 12), "end_at": datetime(2026, 9, 20, 13), "downtime_type": "Частичный"},
        {"id": 6, "start_at": datetime(2026, 9, 20, 12), "end_at": datetime(2026, 9, 20, 13), "downtime_type": "Простой линии"},
        {"id": 7, "start_at": datetime(2026, 9, 20, 12), "end_at": datetime(2026, 9, 20, 13), "downtime_type": "Поломка"},
        {"id": 8, "start_at": None, "end_at": None, "downtime_type": "Без простоя"},
    ]
    factory = MagicMock(return_value=engine)
    monkeypatch.setattr(service, "create_engine", factory)
    monkeypatch.setattr(settings, "downtime_database_url", "mssql+pymssql://user:password@server/EQUIPMENT")
    monkeypatch.setattr(settings, "downtime_query", "")
    status, rows = service.load_downtimes(date(2026, 9, 20), date(2026, 9, 20))
    assert status == "connected"
    assert sum(bool(row.get("data_error")) for row in rows) == 3
    assert rows[1]["downtime_type"] == "unknown"
    assert rows[4]["downtime_type"] == "partial"
    assert rows[5]["downtime_type"] == "full"
    assert rows[6]["downtime_type"] == "unknown"
    assert len(rows) == 7
    sql = str(connection.execute.call_args.args[0])
    assert "EQUIP_START_DATE AS start_at, STOP_DATE AS end_at" in sql
    assert "FIX_STOP_DATE" not in sql
    assert factory.call_args.kwargs["connect_args"]["timeout"] == 15
    engine.dispose.assert_called_once()


def test_source_errors_are_not_returned_with_credentials(monkeypatch):
    monkeypatch.setattr(settings, "downtime_database_url", "mssql+pymssql://user:secret@server/EQUIPMENT")
    monkeypatch.setattr(service, "create_engine", MagicMock(side_effect=RuntimeError("secret")))
    assert service.load_downtimes(date(2026, 9, 20), date(2026, 9, 20)) == ("unavailable", [])


def test_insights_report_invalid_dates_and_do_not_invent_loss_without_tasks(db, monkeypatch):
    line = db.scalar(select(ProductionLine))
    common = {"line_code": line.code, "line_name": line.name, "reason": "Test"}
    monkeypatch.setattr(line_routes, "load_downtimes", lambda *_: ("connected", [
        {**common, "id": "bad", "data_error": "Конец простоя не позже начала", "start_at": datetime(2026, 9, 20, 11), "end_at": datetime(2026, 9, 20, 10)},
        {**common, "id": "full", "start_at": datetime(2026, 9, 20, 10), "end_at": datetime(2026, 9, 20, 11), "downtime_type": "full"},
        {**common, "id": "unknown", "start_at": datetime(2026, 9, 20, 10), "end_at": datetime(2026, 9, 20, 11), "downtime_type": "unknown"},
    ]))
    result = line_routes.line_insights(date(2026, 9, 20), date(2026, 9, 20), db, UserContext("admin", "Admin", "admin"))
    assert len(result["issues"]) == 1
    assert result["issues"][0]["line_id"] == line.id
    assert result["issues"][0]["start_at"] > result["issues"][0]["end_at"]
    assert result["downtimes"][0]["loss_percent"] == 80
    assert result["downtimes"][1]["loss_percent"] is None
    assert all(row["estimated_loss_kg"] is None for row in result["downtimes"])
