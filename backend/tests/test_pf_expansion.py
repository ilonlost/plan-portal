from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import select, func

from test_audit_regressions import db, client
from app.api.routes import plans
from app.models.entities import Product, ProductionPlan, ProductionLine, LineCapability, LineCapacity, ProductionScheduleItem
from app.services.plan_service import PlanService, schedule_item_dict
from app.services.pf_service import preview_expansion, rules_for


@pytest.fixture(autouse=True)
def bom_for_existing_chain_tests(monkeypatch):
    from app.services import bom_volume_service
    # Legacy chain tests focus on scheduling; a deterministic BOM keeps their amounts.
    monkeypatch.setattr(bom_volume_service, "fetch_bom_volume", lambda sku, volume: [
        {"body_nr": code, "comp_qnt": str(volume), "ek_me": "кг"}
        for code in ["4510000047", "1021002013", "1021001493", "PF-NEW"]
    ])


def setup_gp(db, sku="1010023231", line_name="Слойка"):
    line = db.scalar(select(ProductionLine).where(ProductionLine.name == line_name))
    gp = Product(sku=sku, name="ГП", box_weight_kg=Decimal("1"), unit_weight_kg=Decimal("1"))
    plan = ProductionPlan(name="PF test", horizon_start=date(2026, 11, 10), horizon_end=date(2026, 11, 10))
    db.add_all([gp, plan]); db.flush()
    db.add(LineCapability(line_id=line.id, product_id=gp.id, units_per_hour=100))
    db.commit()
    values = dict(product_id=gp.id, line_id=line.id, production_date=date(2026, 11, 10),
                  quantity=Decimal("100"), source_kind="generic", shift="day", expand_pf=True)
    return plan, values, line


def add_pf(db, sku, line, speed=100, box_weight=None):
    product = Product(sku=sku, name="ПФ", box_weight_kg=box_weight)
    db.add(product); db.flush()
    db.add(LineCapability(product_id=product.id, line_id=line.id, units_per_hour=speed))
    db.commit()
    return product


def productions(plan):
    return [row for row in plan.schedule_items if row.schedule_kind == "production" and not row.excluded]


def test_mapping_deduplicates_and_resolves_explicit_offsets():
    assert len(rules_for("1010023231")) == 1
    assert rules_for("1010023231")[0]["lead_days"] == 2
    assert rules_for("1010022357")[0]["lead_days"] == 7
    assert rules_for("1010029651")[0]["lead_days"] == 0
    assert [row["lead_days"] for row in rules_for("1010027200")] == [2, 5]
    assert rules_for("unknown") == []


def test_preview_is_read_only_and_creation_counts_common_capacity(db):
    plan, values, line = setup_gp(db)
    add_pf(db, "4510000047", line, speed=50, box_weight=Decimal("70"))
    # Same-day override proves GP + PF share one resource, with exact 1:1 kg.
    values["pf_tasks"] = [{"sku": "4510000047", "production_date": values["production_date"]}]
    db.add(LineCapacity(line_id=line.id, capacity_date=values["production_date"], shift="day", available_hours=2, available=True, manual_override=True))
    db.commit()
    preview = preview_expansion(db, values)
    assert preview["items"][0]["quantity_kg"] == Decimal("100")
    assert db.scalar(select(func.count(ProductionScheduleItem.id))) == 0
    PlanService(db).create_manual_task(plan, values)
    gp, child = productions(plan)
    assert child.parent_task_id == gp.id
    assert child.source_kind == "pf" and child.quantity_kg == gp.quantity_kg == 100
    assert child.required_hours == 2 and gp.required_hours == 1
    assert child.load_percent >= 150 and gp.load_percent == child.load_percent
    assert schedule_item_dict(child)["source_kind"] == "pf"


def test_disable_and_direct_pf_do_not_expand(db):
    plan, values, line = setup_gp(db)
    PlanService(db).create_manual_task(plan, {**values, "expand_pf": False})
    PlanService(db).create_manual_task(plan, {**values, "source_kind": "pf"})
    assert len(productions(plan)) == 2
    assert all(row.parent_task_id is None for row in productions(plan))


def test_missing_speed_fails_atomically_then_can_be_entered(db):
    plan, values, line = setup_gp(db)
    preview = preview_expansion(db, values)
    assert preview["items"][0]["line_id"] == line.id
    assert preview["items"][0]["errors"]
    with pytest.raises(ValueError, match="скорость ПФ"):
        PlanService(db).create_manual_task(plan, values)
    assert not productions(plan)
    assert not db.scalar(select(Product).where(Product.sku == "4510000047"))
    values["pf_tasks"] = [{"sku": "4510000047", "speed_kg_hour": Decimal("50")}]
    PlanService(db).create_manual_task(plan, values)
    child = next(row for row in productions(plan) if row.parent_task_id)
    assert child.production_date == date(2026, 11, 8)
    assert plan.horizon_start == date(2026, 11, 8)
    assert db.scalar(select(LineCapacity).where(LineCapacity.line_id == line.id, LineCapacity.capacity_date == child.production_date, LineCapacity.shift == "day"))


def test_foreign_component_and_blocked_product_rejected(db):
    plan, values, line = setup_gp(db)
    with pytest.raises(ValueError, match="Состав ПФ"):
        preview_expansion(db, {**values, "pf_tasks": [{"sku": "bogus"}]})
    child = add_pf(db, "4510000047", line)
    child.catalog_status = "blocked"; db.commit()
    with pytest.raises(ValueError, match="заблокирован"):
        PlanService(db).create_manual_task(plan, values)
    assert not productions(plan)


def test_croissant_speed_and_multistage_dates(db):
    plan, values, line = setup_gp(db, "1010027200")
    filling_line = db.scalar(select(ProductionLine).where(ProductionLine.name == "НАЧИНКА"))
    values["pf_tasks"] = [{"sku": "1021001493", "line_id": filling_line.id, "speed_kg_hour": 200}]
    PlanService(db).create_manual_task(plan, values)
    gp, pf, filling = productions(plan)
    assert gp.required_hours == PlanService._production_duration(Decimal(100), Decimal("27.26"))
    assert pf.required_hours == PlanService._production_duration(Decimal(100), Decimal("109.04"))
    assert filling.production_date == pf.production_date - timedelta(days=3)
    assert len(productions(plan)) == 3
    PlanService(db).calculate(plan, [])
    assert gp.required_hours == PlanService._production_duration(Decimal(100), Decimal("27.26"))
    assert pf.required_hours == PlanService._production_duration(Decimal(100), Decimal("109.04"))


def test_parent_edit_delete_and_recalculation_preserve_chain(db):
    plan, values, line = setup_gp(db)
    add_pf(db, "4510000047", line, box_weight=Decimal("70"))
    service = PlanService(db)
    service.create_manual_task(plan, values)
    gp, child = productions(plan)
    service.update_item(gp, {"quantity": Decimal("110"), "production_date": date(2026, 11, 12)})
    assert child.quantity_kg == 110 and child.production_date == date(2026, 11, 10)
    service.calculate(plan, [])
    assert len(productions(plan)) == 2 and child.quantity_kg == 110
    service.calculate(plan, [])
    assert len(productions(plan)) == 2
    service.delete_item(gp)
    assert not productions(plan)


def test_api_preview_create_and_revision(client, db, monkeypatch):
    monkeypatch.setattr(plans, "send_notification", lambda *a, **k: None)
    plan, values, line = setup_gp(db)
    add_pf(db, "4510000047", line)
    values.update(quantity=100, production_date="2026-11-10")
    response = client.post(f"/plans/{plan.id}/pf-preview", json=values)
    assert response.status_code == 200, response.text
    assert len(response.json()["items"]) == 1
    assert client.post(f"/plans/{plan.id}/items", json=values).status_code == 428
    revision = plan.revision
    response = client.post(f"/plans/{plan.id}/items", json=values, headers={"If-Match": str(revision)})
    assert response.status_code == 200, response.text
    assert len([row for row in response.json()["items"] if row["source_kind"] == "pf"]) == 1
    assert client.post(f"/plans/{plan.id}/items", json=values, headers={"If-Match": str(revision)}).status_code == 409


def test_pf_label_in_exports(db, monkeypatch):
    from openpyxl import load_workbook
    from app.core.config import settings
    from app.services.export_service import ExcelExportService
    from app.services.notification_service import build_plan_xlsx, build_plan_email_html

    plan, values, line = setup_gp(db)
    add_pf(db, "4510000047", line)
    PlanService(db).create_manual_task(plan, values)
    child = next(row for row in productions(plan) if row.parent_task_id)
    serialized = schedule_item_dict(child)
    monkeypatch.setattr(settings, "plan_export_template", "")
    book = load_workbook(BytesIO(ExcelExportService().build([serialized])))
    assert book["План выгрузки"].cell(2, 26).value == "ПФ"
    book = load_workbook(BytesIO(build_plan_xlsx([serialized], "production")))
    assert book.active.cell(2, 9).value == "ПФ"
    for audience in ("production", "warehouse", "materials"):
        html = build_plan_email_html({}, "План", date(2026, 11, 8), date(2026, 11, 10), [serialized], audience)
        assert "ПФ · " in html


def test_migration_preserves_existing_tasks():
    import sqlalchemy as sa
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = Path(__file__).parents[1] / "alembic/versions/20261108_0024_pf_expansion.py"
    spec = importlib.util.spec_from_file_location("pf_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE production_schedule_items (id INTEGER PRIMARY KEY, quantity NUMERIC NOT NULL)")
        connection.exec_driver_sql("INSERT INTO production_schedule_items VALUES (1, 123.45)")
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            row = connection.exec_driver_sql("SELECT quantity, parent_task_id, pf_details FROM production_schedule_items").one()
            assert row == (123.45, None, None)
            migration.downgrade()
            assert connection.exec_driver_sql("SELECT quantity FROM production_schedule_items").scalar() == 123.45
