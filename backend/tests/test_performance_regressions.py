from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import event, select

from app.api.routes.lines import list_capacities, list_lines
from app.core.security import UserContext
from app.models.entities import LineCapacity, ProductionScheduleItem
from app.services.plan_service import PlanService
from tests.test_audit_regressions import client, db, make_plan


def test_calculation_does_not_load_unrelated_historical_capacities(db):
    plan, demand = make_plan(db)
    line_id = db.scalar(select(LineCapacity.line_id).limit(1))
    db.add(LineCapacity(line_id=line_id, capacity_date=date(2000, 1, 1), shift="day", available_hours=1))
    db.commit()
    db.expunge_all()
    loaded_dates = []

    def track(session, instance):
        if isinstance(instance, LineCapacity):
            loaded_dates.append(instance.capacity_date)

    event.listen(db, "loaded_as_persistent", track)
    try:
        service = PlanService(db)
        service.calculate(service.active_plan(), service._latest_source_demands())
    finally:
        event.remove(db, "loaded_as_persistent", track)
    assert loaded_dates
    assert date(2000, 1, 1) not in loaded_dates
    assert db.scalar(select(LineCapacity.id).where(LineCapacity.capacity_date == date(2000, 1, 1)))


def test_manual_item_outside_horizon_keeps_its_capacity_override(db):
    plan, demand = make_plan(db)
    item = next(row for row in plan.schedule_items if row.schedule_kind == "production")
    outside = plan.horizon_end + timedelta(days=7)
    item.production_date = outside
    item.required_hours = Decimal("2")
    db.add(LineCapacity(line_id=item.line_id, capacity_date=outside, shift=item.shift, available_hours=4, manual_override=True))
    db.flush()
    PlanService(db).recalculate_load(plan)
    assert item.load_percent == Decimal("50.00")


def test_revision_probe_tracks_changes_without_loading_schedule(client, db):
    assert client.get("/plans/active/revision").status_code == 404
    plan, _ = make_plan(db)
    old_version = plan.revision
    loaded = []
    event.listen(db.bind, "before_cursor_execute", lambda conn, cursor, statement, parameters, context, many: loaded.append(statement))
    assert client.get("/plans/active/revision").json() == {"id": plan.id, "version": old_version}
    assert not any("production_schedule_items" in sql for sql in loaded)
    plan.revision += 1
    db.commit()
    assert client.get("/plans/active/revision").json()["version"] == old_version + 1
    client.cookies.clear()
    assert client.get("/plans/active/revision").status_code == 401


def test_line_and_capacity_summaries_use_bounded_queries(db):
    plan, _ = make_plan(db)
    item = next(row for row in plan.schedule_items if row.schedule_kind == "production")
    item.production_date = date.today()
    item.load_percent = 75
    db.add(LineCapacity(line_id=item.line_id, capacity_date=date.today(), shift=item.shift, available_hours=4))
    db.commit()
    statements = []

    def track(conn, cursor, statement, parameters, context, many):
        statements.append(statement)

    event.listen(db.bind, "before_cursor_execute", track)
    try:
        user = UserContext("regular.admin", "Administrator", "admin")
        lines = list_lines(db, user)
        assert len(statements) == 4
        line = next(row for row in lines if row["id"] == item.line_id)
        assert line["product_count"] == 1 and line["today_load"] == 75
        statements.clear()
        capacities = list_capacities(date.today(), 7, db, user)
        assert len(statements) == 3
        assert next(row for row in capacities if row["line_id"] == item.line_id)["load_percent"] == 75
    finally:
        event.remove(db.bind, "before_cursor_execute", track)
