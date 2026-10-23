from datetime import date, datetime, timedelta
from decimal import Decimal
from io import BytesIO

import pytest
from openpyxl import load_workbook
from sqlalchemy import select

from test_audit_regressions import db, client, make_plan
from app.api.routes import catalog, plans
from app.models.entities import LineCapability, ProductionLine, ProductionScheduleItem
from app.services.plan_service import PlanService, schedule_item_dict
from app.services.line_import_service import filter_line_preview
from app.services.notification_service import build_plan_xlsx, build_plan_email_html
from app.schemas.common import ImportPreview, ImportRow


CYCLE = {"enabled": True, "start_time": "18:00", "duration_hours": 24}


def production(plan, demand):
    return [r for r in plan.schedule_items if r.demand_item_id == demand.id and r.schedule_kind == "production" and not r.excluded]


@pytest.mark.parametrize("status", ["АЗ", "По графику"])
def test_sandwich_source_dm_and_exact_production_window(db, status):
    plan, demand = make_plan(db)
    line = production(plan, demand)[0].line
    line.planning_settings = {"production_cycle": CYCLE}
    demand.product.advance_status = status
    demand.marking_date = demand.source_plan_date = date(2026, 10, 23)
    PlanService(db).calculate(plan, [demand])
    rows = production(plan, demand)
    assert rows and all(r.production_date == date(2026, 10, 22) for r in rows)
    item = schedule_item_dict(rows[0])
    assert item["marking_date"] == date(2026, 10, 23)
    assert item["shipping_date"] == date(2026, 10, 22)
    assert item["production_window_start"] == datetime(2026, 10, 21, 18)
    assert item["production_window_end"] == datetime(2026, 10, 22, 18)
    book = load_workbook(BytesIO(build_plan_xlsx([item], "production")))
    assert datetime(2026, 10, 21, 18) in [c.value for c in book.active[2]]
    assert datetime(2026, 10, 22, 18) in [c.value for c in book.active[2]]
    preview = ImportPreview(file_name="OHL.xlsx", template_type="ohl_daily", mapping_code="default_v1", total_rows=1, valid_rows=1, invalid_rows=0, columns=[], rows=[ImportRow(row_number=2, sku=demand.sku, quantity=10, requested_date=date(2026,10,23), due_date=date(2026,10,23), marking_date=date(2026,10,23), valid=True)])
    assert len(filter_line_preview(db, preview, line.id, date(2026,10,22), date(2026,10,22)).rows) == 1


def test_catalog_status_change_rebuilds_cycle_tasks_and_discards_stale_az_override(client, db, monkeypatch):
    monkeypatch.setattr(catalog, "send_notification", lambda *a, **k: None)
    plan, demand = make_plan(db)
    line = production(plan, demand)[0].line
    line.name = "Салаты"  # A normal line with a configured window still obeys its catalogue rule.
    line.planning_settings = {"production_cycle": CYCLE}
    demand.marking_date = demand.source_plan_date
    demand.raw_data = {"az_override_production_date": "2026-09-07", "az_override_advance_status": "АЗ"}
    demand.product.advance_status = "АЗ"
    db.commit()
    for status, expected in [("По графику", date(2026,9,8)), ("АЗ", date(2026,9,7)), ("По графику", date(2026,9,8))]:
        response = client.patch(f"/catalog/products/{demand.product_id}", json={"advance_status": status})
        assert response.status_code == 200, response.text
        rows = production(plan, demand)
        assert rows and all(r.production_date == expected for r in rows)
        assert all(schedule_item_dict(r)["shipping_date"] == expected for r in rows)
        assert all(r.marking_date == date(2026,9,8) for r in rows)
    assert "az_override_production_date" not in demand.raw_data


@pytest.mark.parametrize("source", ["ohl", "zam", "generic", "pf"])
def test_bulka_three_dates_on_creation_move_and_rebuild(client, db, monkeypatch, source):
    monkeypatch.setattr(plans, "send_notification", lambda *a, **k: None)
    plan, demand = make_plan(db)
    line = db.scalar(select(ProductionLine).where(ProductionLine.name == "Булка"))
    db.add(LineCapability(line_id=line.id, product_id=demand.product_id, units_per_hour=100))
    demand.product.advance_status = "АЗ"; db.commit()
    response = client.post(f"/plans/{plan.id}/items", headers={"If-Match":str(plan.revision)}, json={"product_id":demand.product_id,"line_id":line.id,"production_date":"2026-10-23","marking_date":"2026-10-24","quantity":10,"source_kind":source})
    assert response.status_code == 200, response.text
    item = next(r for r in plan.schedule_items if r.line_id == line.id and r.source == "manual" and r.schedule_kind == "production")
    result = schedule_item_dict(item)
    assert result["production_date"] == result["marking_date"] == result["shipping_date"]
    moved = client.patch(f"/plans/{plan.id}/items/{item.id}", headers={"If-Match":str(plan.revision)}, json={"production_date":"2026-10-25"})
    assert moved.status_code == 200, moved.text
    assert item.marking_date == item.production_date == date(2026,10,25)
    assert schedule_item_dict(item)["shipping_date"] == date(2026,10,25)


@pytest.mark.parametrize("kind", ["cleaning", "maintenance", "trial", "gas", "startup", "changeover"])
def test_technical_event_without_product_or_comment_survives_rebuild(client, db, monkeypatch, kind):
    monkeypatch.setattr(plans, "send_notification", lambda *a, **k: None)
    plan, demand = make_plan(db)
    line_id = production(plan, demand)[0].line_id
    result = client.post(f"/plans/{plan.id}/events", headers={"If-Match":str(plan.revision)}, json={"line_id":line_id,"production_date":"2026-10-23","schedule_kind":kind,"duration_hours":1.5})
    assert result.status_code == 200, result.text
    event = next(r for r in plan.schedule_items if r.schedule_kind == kind and r.source == "manual")
    assert event.product_id is None and event.required_hours == Decimal("1.5") and event.reason
    assert plan.horizon_end >= date(2026,10,23)
    PlanService(db).calculate(plan, [demand])
    assert event in plan.schedule_items and not event.excluded
    assert event.required_hours == Decimal("1.5")
    assert any(i["id"] == event.id for w in client.get("/plans/active/matrix?start=2026-10-23&days=1").json()["workshops"] for l in w["lines"] for c in l["cells"] for i in c["items"])


def test_date_upgrade_once_normalizes_bulka_and_preserves_technical_events(db):
    plan, demand = make_plan(db)
    line = production(plan, demand)[0].line
    line.planning_settings = {"production_cycle": CYCLE}
    demand.marking_date = demand.source_plan_date = date(2026,10,23)
    PlanService(db).create_event(plan,{"line_id":line.id,"production_date":date(2026,10,23),"schedule_kind":"cleaning","duration_hours":1})
    event = next(r for r in plan.schedule_items if r.schedule_kind == "cleaning")
    assert PlanService(db).apply_task_date_rules_upgrade()
    rows = production(plan,demand)
    assert rows and all(r.production_date == date(2026,10,22) for r in rows)
    assert event in plan.schedule_items and not event.excluded
    revision = plan.revision
    assert not PlanService(db).apply_task_date_rules_upgrade()
    assert plan.revision == revision
