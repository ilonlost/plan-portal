"""Release verification on an explicitly isolated copy of the production DB."""
import json
import os
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.engine import make_url
from app.core.config import settings

url = make_url(settings.database_url)
name = os.environ.get("RELEASE_CHECK_DATABASE")
if name != "plan_release_0213_qa" or name == url.database:
    raise RuntimeError("Requires the isolated plan_release_0213_qa database")
settings.database_url = url.set(database=name).render_as_string(hide_password=False)

from sqlalchemy import text
from sqlalchemy.orm import Session
from app.db.session import engine
from app.services.plan_service import PlanService, schedule_item_dict
from app.services.task_date_service import is_bulka, uses_sandwich_window


def fingerprint(conn):
    tables = ["production_schedule_items", "production_plans", "production_plan_versions", "demand_items", "products", "line_capabilities", "production_lines", "line_capacities", "imported_orders", "portal_settings"]
    return {t: tuple(conn.execute(text(f"SELECT count(*), md5(COALESCE(string_agg(row_to_json(t)::text, '' ORDER BY t.id), '')) FROM {t} t")).one()) for t in tables}


def volume(plan):
    totals = defaultdict(Decimal)
    for row in plan.schedule_items:
        if row.schedule_kind == "production" and not row.excluded:
            totals[(row.demand_item_id, row.source_kind)] += Decimal(row.quantity)
    return totals


with engine.connect() as conn:
    before = fingerprint(conn); conn.rollback()
    outer = conn.begin()
    db = Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    try:
        service = PlanService(db)
        plan = service.active_plan()
        old_volume = volume(plan)
        frozen = {r.id: (r.line_id, r.production_date, r.marking_date, r.quantity, r.execution_status, r.execution_note)
                  for r in plan.schedule_items if r.schedule_kind == "production" and r.source_kind != "ohl" and not is_bulka(r.line)}
        manual_events = {r.id:(r.line_id,r.production_date,r.schedule_kind,r.required_hours,r.reason)
                         for r in plan.schedule_items if r.schedule_kind != "production" and r.source != "auto"}
        assert service.apply_task_date_rules_upgrade()
        assert volume(plan) == old_volume, "Released volume changed"
        assert frozen == {r.id:(r.line_id,r.production_date,r.marking_date,r.quantity,r.execution_status,r.execution_note)
                          for r in plan.schedule_items if r.id in frozen}, "Non-OHL cards changed"
        assert manual_events == {r.id:(r.line_id,r.production_date,r.schedule_kind,r.required_hours,r.reason)
                                for r in plan.schedule_items if r.id in manual_events}, "Manual events changed"
        checked = defaultdict(int)
        examples = []
        for row in plan.schedule_items:
            if row.schedule_kind != "production" or row.excluded or not row.production_date:
                continue
            data = schedule_item_dict(row)
            if is_bulka(row.line):
                assert data["production_date"] == data["marking_date"] == data["shipping_date"]
                checked["bulka"] += 1
            elif uses_sandwich_window(row.line) and row.source_kind == "ohl":
                assert row.production_date == row.marking_date - timedelta(days=1)
                end = datetime.combine(row.production_date, datetime.min.time()).replace(hour=18)
                assert data["production_window_end"] == end
                assert data["production_window_start"] == end - timedelta(days=1)
                checked["sandwich"] += 1
                if row.marking_date == date(2026,10,23) and not examples:
                    examples.append({k:str(data[k]) for k in ("sku","production_date","marking_date","shipping_date","production_window_start","production_window_end")})
            elif row.product and row.product.advance_status == "По графику" and row.source_kind == "ohl":
                assert data["shipping_date"] == row.marking_date
                checked["scheduled"] += 1
        assert checked["sandwich"] and checked["bulka"]
        revision = plan.revision
        assert not service.apply_task_date_rules_upgrade() and plan.revision == revision
        line_id = next(r.line_id for r in plan.schedule_items if uses_sandwich_window(r.line))
        for kind in ("maintenance", "cleaning", "trial", "gas", "startup", "changeover"):
            service.create_event(plan, {"line_id":line_id,"production_date":date(2026,10,23),"schedule_kind":kind,"duration_hours":Decimal("0.5")})
            assert any(r.schedule_kind == kind and r.source == "manual" and r.required_hours == Decimal("0.5") for r in plan.schedule_items)
        print(json.dumps({"verified":dict(checked),"unchanged_non_ohl_cards":len(frozen),"preserved_manual_events":len(manual_events),"same_released_volume":True,"technical_types_created":6,"example":examples},ensure_ascii=False))
    finally:
        db.close(); outer.rollback()
    assert fingerprint(conn) == before
    print("ISOLATED_QA_ROLLBACK_VERIFIED")
