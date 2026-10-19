from datetime import date
from decimal import Decimal
from pathlib import Path
import importlib.util

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select, delete

from test_audit_regressions import db, client, make_plan
from test_line_import_scope import two_lines
from app.models.entities import DemandItem, PortalSetting, ProductionLine, ScheduleStatus, User
from app.services.plan_service import PlanService
from app.services.settings_service import get_planner_configuration, save_planner_configuration
from app.services.planning_engine import PlanningEngine, DemandInput, CapabilityInput, CapacityInput


@pytest.mark.parametrize("kind", ["ohl", "zam"])
@pytest.mark.parametrize("mandatory", [False, True])
def test_source_volume_on_closed_line_is_controlled_without_inflating_demand(kind, mandatory):
    day = date(2026, 10, 19)
    result = PlanningEngine().plan(
        [DemandInput(1, 1, "SKU", Decimal(123), day, day, source_kind=kind)],
        [CapabilityInput(1, 1, Decimal(100), batch_quantum_kg=Decimal(10))],
        [CapacityInput(1, day, Decimal(0), available=False)], day,
        complete_ohl=mandatory, complete_zam=mandatory,
    )
    assert sum(row.quantity for row in result) == 130  # Only the existing quantum rounding.
    assert all((row.production_date == day) == mandatory for row in result)
    if mandatory:
        assert all(row.status == "warning" for row in result)
        if kind == "zam":
            assert all(row.marking_date == row.production_date for row in result)


def test_planner_can_disable_and_enable_zam_with_immediate_recalculation(client, db, monkeypatch):
    plan, _, _, _, _ = two_lines(db, monkeypatch)
    demand = db.scalar(select(DemandItem).where(DemandItem.source_kind == "zam"))
    demand.quantity = demand.source_quantity = 500
    db.commit()
    service = PlanService(db); service.calculate(plan, service._latest_source_demands())
    def remaining():
        return sum(row.quantity for row in plan.schedule_items if row.source_kind == "zam" and not row.excluded and not row.production_date)
    assert remaining() == 0
    assert any(row.source_kind == "zam" and row.status == ScheduleStatus.WARNING for row in plan.schedule_items)
    def ohl_cards():
        return {row.id: (row.line_id, row.production_date, row.marking_date, row.quantity, row.required_hours, row.sequence, row.execution_status) for row in plan.schedule_items if row.source_kind == "ohl" and row.schedule_kind == "production"}
    before_ohl = ohl_cards()
    response = client.put("/planner-admin/placement", json={"complete_ohl": True, "complete_zam": False, "plan_version": plan.revision})
    assert response.status_code == 200, response.text
    assert remaining() == 400
    assert ohl_cards() == before_ohl
    assert not client.get("/planner-admin").json()["placement"]["complete_zam"]
    assert client.put("/planner-admin/placement", json={"complete_ohl": True, "complete_zam": True, "plan_version": 999}).status_code == 409
    assert not get_planner_configuration(db)["placement"]["complete_zam"]
    response = client.put("/planner-admin/placement", json={"complete_ohl": True, "complete_zam": True, "plan_version": plan.revision})
    assert response.status_code == 200 and remaining() == 0
    assert ohl_cards() == before_ohl
    db.scalar(select(User)).role = "viewer"; db.commit()
    assert client.put("/planner-admin/placement", json={"complete_ohl": True, "complete_zam": False, "plan_version": plan.revision}).status_code == 403


def test_new_hot_line_migration_is_idempotent_and_keeps_other_lines(db):
    hot = db.scalar(select(ProductionLine).where(ProductionLine.name == "Горячий цех"))
    db.delete(hot); db.commit()
    before = {row.id: {c.name: getattr(row, c.name) for c in ProductionLine.__table__.columns} for row in db.scalars(select(ProductionLine))}
    path = Path(__file__).parents[1] / "alembic/versions/20261018_0021_hot_kitchen_line.py"
    spec = importlib.util.spec_from_file_location("hot_line_migration", path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(db.connection())):
        module.upgrade(); module.upgrade()
    db.expire_all()
    rows = list(db.scalars(select(ProductionLine)))
    assert len(rows) == len(before) + 1
    hot = next(row for row in rows if row.name == "Горячий цех")
    assert hot.workshop_code == "KC" and hot.status == "active" and not hot.csb_line_code
    assert not hot.capabilities
    assert {row.id: {c.name: getattr(row, c.name) for c in ProductionLine.__table__.columns} for row in rows if row.id in before} == before


def test_upgrade_runs_once_and_preserves_disabled_setting(db):
    plan, _ = make_plan(db)
    save_planner_configuration(db, {"placement": {"complete_zam": False}}, "test"); db.commit()
    service = PlanService(db)
    assert service.apply_complete_zam_upgrade()
    revision = plan.revision
    assert not service.apply_complete_zam_upgrade() and plan.revision == revision
    assert get_planner_configuration(db)["placement"] == {"complete_ohl": True, "complete_zam": False}
