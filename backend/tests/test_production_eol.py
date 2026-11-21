from datetime import date, datetime
from decimal import Decimal as D
from io import BytesIO
from unittest.mock import MagicMock

from openpyxl import load_workbook
from sqlalchemy import select

from app.core.config import settings
from app.models.entities import User
from app.services import production_eol as service
from tests.test_audit_regressions import client, db


def raw(**changes):
    return dict(center=5850, center_name=" Панировка ", sku=1012345678, product_name="=SUM(A1:A3)",
                order_number=123456, order_line=1, variant=2, lot="00123456 ", plan_date=20261110,
                planned_start_date=20261110, production_date=datetime(2026, 11, 11),
                plan_quantity=D("100"), quantity_unit="шт", storage_unit="кр", plan_kg=D("26"),
                kg_per_unit=D("0.260"), day_quantity=D("40"), night_quantity=None,
                day_storage=D("10"), night_storage=None) | changes


def test_units_null_shifts_negative_corrections_and_unknown_factor():
    row = service.normalize_row(raw(night_quantity=D("-2")))
    assert row["day_kg"] == D("10.400") and row["night_kg"] == D("-0.520")
    assert row["total_kg"] == D("9.880")
    assert row["lot"] == "00123456" and row["production_date"] == date(2026, 11, 11)
    assert service.normalize_row(raw())["night_kg"] == 0
    unknown = service.normalize_row(raw(kg_per_unit=None, plan_date=0))
    assert unknown["total_kg"] is None and unknown["warning"] and unknown["plan_date"] is None
    assert service.totals([unknown])["total_kg"] is None
    assert service.totals([])["total_kg"] == 0


def test_repeated_order_dates_and_filters_do_not_duplicate_plan(monkeypatch):
    rows = [service.normalize_row(raw()), service.normalize_row(raw(production_date=datetime(2026, 11, 12))),
            service.normalize_row(raw(center=9930, order_number=123457, kg_per_unit=None))]
    monkeypatch.setattr(service, "read_rows", lambda *args: rows)
    data = service.report(date(2026, 11, 11), date(2026, 11, 12))
    assert data["summary"]["total_kg"] == D("20.8")
    assert data["summary"]["orders"] == 2 and data["summary"]["missing_weight_rows"] == 1
    assert "plan_kg" not in data["summary"]
    assert len(data["by_day"]) == 2 and len(data["by_center"]) == 2
    assert data["by_task"][0]["plan_kg"] == D("26")
    assert data["by_task"][0]["fact_kg"] == D("20.8")
    assert data["by_task"][0]["status"] == "pending"
    assert data["by_task"][1]["status"] == "unknown"
    filtered = service.report(date(2026, 11, 11), date(2026, 11, 12), "5850", "00123456")
    assert filtered["total_count"] == 2 and len(filtered["centers"]) == 2
    assert len(filtered["by_task"]) == 1 and filtered["by_task"][0]["center"] == "5850"
    assert service.report(date(2026, 11, 11), date(2026, 11, 12), search="%")["total_count"] == 0


def test_query_is_read_only_bounded_and_uses_end_exclusive(monkeypatch):
    engine = MagicMock()
    connection = engine.connect.return_value.__enter__.return_value
    connection.execute.return_value.mappings.return_value.all.return_value = [raw()]
    monkeypatch.setattr(settings, "production_eol_database_url", "mssql+pymssql://u:p@host/db")
    monkeypatch.setattr(service, "create_engine", lambda *args, **kwargs: engine)
    service.read_rows(date(2026, 10, 31), date(2026, 11, 1))
    sql, params = connection.execute.call_args.args
    assert "FROM [mtz].[PLAN_FACT_PRODUCTION_EOL]" in str(sql)
    assert "KA0133_PLAN_DATUM >= :start_plan AND KA0133_PLAN_DATUM < :end_plan" in str(sql)
    assert params["start_plan"] == 20261031 and params["end_plan"] == 20261102
    assert params["end_at"] == datetime(2026, 11, 2)
    assert params["max_rows"] == service.MAX_ROWS + 1
    engine.dispose.assert_called_once()


def test_plan_date_controls_chart_tasks_and_keeps_actual_date_in_export(monkeypatch):
    rows = [service.normalize_production_row(raw(plan_date=20261101, production_date=datetime(2026, 10, 31))),
            service.normalize_production_row(raw(plan_date=20261101, production_date=datetime(2026, 11, 12)))]
    monkeypatch.setattr(service, "read_rows", lambda *args: rows)
    data = service.report(date(2026, 11, 1), date(2026, 11, 1))
    assert data["summary"]["total_kg"] == D("20.8")
    assert len(data["by_day"]) == 1 and data["by_day"][0]["date"] == date(2026, 11, 1)
    task = data["by_task"][0]
    assert task["plan_kg"] == D("26") and task["fact_kg"] == D("20.8")
    assert len(task["fact_by_day"]) == 1 and task["fact_by_day"][0]["date"] == date(2026, 11, 1)
    book = load_workbook(BytesIO(service.export_xlsx(data)))
    assert book["По датам"]["A1"].value == "Плановая дата задания"
    assert book["По датам"]["A2"].value == datetime(2026, 11, 1)
    assert book["Факт производства"]["A2"].value == datetime(2026, 10, 31)
    assert book["Факт производства"]["J2"].value == datetime(2026, 11, 1)
    book.close()


def test_api_pagination_complete_totals_and_export(client, monkeypatch):
    monkeypatch.setattr(service, "read_rows", lambda *args: [service.normalize_row(raw()) for _ in range(105)])
    url = "/production-fact/report?start=2026-11-11&end=2026-11-12"
    response = client.get(url + "&offset=100")
    assert response.status_code == 200
    assert len(response.json()["rows"]) == 5 and response.json()["total_count"] == 105
    assert response.json()["summary"]["total_kg"] == 1092
    assert response.json()["by_task"][0]["fact_kg"] == 1092
    assert response.json()["by_task"][0]["plan_kg"] == 26
    response = client.get("/production-fact/report/export.xlsx?start=2026-11-11&end=2026-11-12&offset=100")
    assert response.status_code == 200
    with BytesIO(response.content) as content:
        book = load_workbook(content)
        assert book.sheetnames == ["Сводка", "По линиям", "По датам", "Факт производства"]
        sheet = book["Факт производства"]
        assert sheet.max_row == 106 and sheet.freeze_panes == "A2"
        assert sheet["E2"].data_type == "s" and sheet["E2"].value == "=SUM(A1:A3)"
        assert sheet["I2"].value == "00123456" and sheet["D2"].data_type == "s"
        assert sheet["U2"].value == 10.4
        book.close()


def test_api_failure_validation_and_permissions(client, db, monkeypatch):
    monkeypatch.setattr(settings, "production_eol_database_url", "")
    assert client.get("/production-fact/report").status_code == 503
    assert client.get("/production-fact/report/export.xlsx").status_code == 503
    for query in ("start=2026-11-12&end=2026-11-11", "start=2026-01-01&end=2026-11-11", "center=evil", "offset=-1", "limit=501"):
        assert client.get("/production-fact/report?" + query).status_code == 422
    user = db.scalar(select(User).where(User.username == "regular.admin"))
    user.role = "viewer"
    db.commit()
    assert client.get("/production-fact/report").status_code == 403
    assert client.get("/production-fact/report/export.xlsx").status_code == 403


def test_driver_errors_do_not_leak_credentials(client, monkeypatch):
    monkeypatch.setattr(settings, "production_eol_database_url", "mssql+pymssql://u:SECRET@server/db")
    def broken(*args, **kwargs):
        raise RuntimeError("SECRET SQL driver details")
    monkeypatch.setattr(service, "create_engine", broken)
    response = client.get("/production-fact/report")
    assert response.status_code == 503 and "SECRET" not in response.text


def test_task_completion_repeated_lots_corrections_and_uncertain_plans():
    normalize = lambda **changes: service.normalize_row(raw(**changes))
    rows = [normalize(day_quantity=D("100")), normalize(lot="other", day_quantity=D("10"))]
    task = service.task_totals(rows)[0]
    assert task["plan_kg"] == D("26") and task["fact_kg"] == D("28.6")
    assert task["status"] == "completed"
    assert service.task_totals([normalize(day_quantity=D("100"))])[0]["status"] == "completed"
    corrected = service.task_totals(rows + [normalize(day_quantity=D("-20"))])[0]
    assert corrected["status"] == "pending" and corrected["fact_kg"] == D("23.4")
    unknown = service.task_totals(rows + [normalize(kg_per_unit=None)])[0]
    assert unknown["status"] == "unknown" and unknown["fact_kg"] is None
    conflicting = service.task_totals(rows + [normalize(plan_kg=D("30"))])[0]
    assert conflicting["status"] == "unknown" and conflicting["plan_kg"] is None
    for plan in (None, D("0"), D("-1")):
        assert service.task_totals([normalize(plan_kg=plan)])[0]["status"] == "unknown"
    separate = service.task_totals(rows + [normalize(order_line=2), normalize(sku="other")])
    assert len(separate) == 3


def test_task_daily_facts_and_percentages_preserve_plan_and_unknown_values():
    rows = [service.normalize_row(raw(day_quantity=D("60"))),
            service.normalize_row(raw(production_date=datetime(2026, 11, 12), day_quantity=D("50")))]
    task = service.task_totals(rows)[0]
    assert task["plan_kg"] == D("26") and task["progress_percent"] == D("110")
    assert task["status"] == "completed"
    assert [day["date"] for day in task["fact_by_day"]] == [date(2026, 11, 11), date(2026, 11, 12)]
    assert [day["fact_kg"] for day in task["fact_by_day"]] == [D("15.6"), D("13")]
    assert [day["progress_percent"] for day in task["fact_by_day"]] == [D("60"), D("50")]
    assert all(day["status"] == "pending" for day in task["fact_by_day"])
    unknown = service.task_totals(rows + [service.normalize_row(raw(kg_per_unit=None))])[0]
    assert unknown["progress_percent"] is None
    assert unknown["fact_by_day"][0]["progress_percent"] is None
    assert unknown["fact_by_day"][1]["progress_percent"] == D("50")
    assert service.task_progress(D("26"), D("-2.6"))["progress_percent"] == D("-10")
    assert service.task_progress(D("26"), D("0"))["progress_percent"] == D("0")
    assert service.task_progress(D("0"), D("10"))["progress_percent"] is None
