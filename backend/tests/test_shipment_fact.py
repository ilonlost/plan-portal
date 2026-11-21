from datetime import date, datetime
from decimal import Decimal as D
from io import BytesIO
from unittest.mock import MagicMock

import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from app.core.config import settings
from app.models.entities import User
from app.services import production_eol, shipment_fact as service
from app.services.tail_buffer_service import SourceError
from tests.test_audit_regressions import client, db
from tests.test_production_eol import raw


def movement(**changes):
    return raw(**changes)


def test_shipment_uses_source_weight_plan_date_and_keeps_corrections():
    data = movement(day_quantity=None, night_quantity=D("-2"), plan_kg=D("999"))
    row = service.normalize_row(data)
    assert row["kg_per_unit"] == D("0.26")
    assert row["day_kg"] == 0 and row["night_kg"] == D("-0.52")
    assert row["production_date"] == date(2026, 11, 11)
    assert row["report_date"] == date(2026, 11, 10)
    for plan, units in ((None, D("100")), (D("26"), None), (D("26"), D("0")), (D("-1"), D("100"))):
        assert service.normalize_row(movement(plan_kg=plan, plan_quantity=units))["total_kg"] == D("10.4")
    assert service.normalize_row(movement(kg_per_unit=None))["total_kg"] is None
    data["plan_date"] = 0
    with pytest.raises(SourceError):
        service.normalize_row(data)


def test_shipment_uses_history_and_plan_date(monkeypatch):
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.mappings.return_value.all.return_value = [movement()]
    monkeypatch.setattr(settings, "production_eol_database_url", "mssql+pymssql://u:p@host/db")
    monkeypatch.setattr(production_eol, "create_engine", lambda *args, **kwargs: engine)
    service.read_rows(date(2026, 11, 11), date(2026, 11, 12))
    query, params = connection.execute.call_args.args
    assert "FROM [mtz].[PLAN_FACT_PRODUCTION_PERIOD]" in str(query)
    assert "PLAN_FACT_PRODUCTION_EOL" not in str(query) and "L52_DATE_MOVE" not in str(query)
    assert "PFP_PLAN_DATUM >= :start_plan AND PFP_PLAN_DATUM < :end_plan" in str(query)
    assert params["start_plan"] == 20261111 and params["end_plan"] == 20261113
    assert params["max_rows"] == production_eol.MAX_ROWS + 1
    engine.dispose.assert_called_once()


def test_shipment_api_is_separate_aggregates_all_rows_and_exports(client, monkeypatch):
    rows = [service.normalize_row(movement(plan_date=20261111, production_date=datetime(2026, 10, 31))),
            service.normalize_row(movement(plan_date=20261111, production_date=datetime(2026, 11, 13)))]
    monkeypatch.setattr(service, "read_rows", lambda *args: rows)
    monkeypatch.setattr(production_eol, "read_rows", lambda *args: [])
    url = "/production-fact/shipment/report?start=2026-11-11&end=2026-11-12&center=5850&search=123456"
    response = client.get(url + "&limit=1")
    assert response.status_code == 200
    data = response.json()
    assert data["source"] == service.SOURCE and len(data["rows"]) == 1
    assert data["total_count"] == 2 and data["summary"]["total_kg"] == 20.8
    assert data["by_task"][0]["plan_kg"] == 26
    assert data["by_task"][0]["progress_percent"] == 80
    assert len(data["by_task"][0]["fact_by_day"]) == 1
    assert data["by_task"][0]["fact_by_day"][0]["date"] == "2026-11-11"
    assert data["by_day"][0]["total_kg"] == data["summary"]["total_kg"]
    production = client.get('/production-fact/report?start=2026-11-11&end=2026-11-12').json()
    assert production["source"] == production_eol.SOURCE and production["total_count"] == 0
    response = client.get(url.replace('/report?', '/report/export.xlsx?'))
    assert response.status_code == 200
    book = load_workbook(BytesIO(response.content))
    assert book.sheetnames[-1] == "Факт отгрузки"
    assert book["Факт отгрузки"]["A1"].value == "Фактическая дата из источника"
    assert book["Факт отгрузки"]["A2"].value == datetime(2026, 10, 31)
    assert book["По датам"]["A1"].value == "Плановая дата задания"
    assert book["По датам"]["A2"].value == datetime(2026, 11, 11)
    assert book["Факт отгрузки"].max_row == 3
    assert book["Факт отгрузки"]["E2"].data_type == "s"
    assert any(row[0].value == "Источник" and row[1].value == service.SOURCE for row in book["Сводка"])
    book.close()


def test_shipment_failure_validation_and_permissions(client, db, monkeypatch):
    monkeypatch.setattr(settings, "production_eol_database_url", "")
    assert client.get('/production-fact/shipment/report').status_code == 503
    assert client.get('/production-fact/shipment/report/export.xlsx').status_code == 503
    for query in ('start=2026-11-12&end=2026-11-11', 'center=bad', 'limit=501'):
        assert client.get('/production-fact/shipment/report?' + query).status_code == 422
    user = db.scalar(select(User).where(User.username == "regular.admin"))
    user.role = "viewer"
    db.commit()
    assert client.get('/production-fact/shipment/report').status_code == 403
    assert client.get('/production-fact/shipment/report/export.xlsx').status_code == 403
