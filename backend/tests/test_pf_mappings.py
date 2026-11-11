from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, func

from test_audit_regressions import db, client
from test_pf_expansion import setup_gp, add_pf, productions
from app.models.entities import AuditEvent, LineCapability, PortalSetting, ProductionLine, User
from app.services.pf_service import preview_expansion
from app.services.plan_service import PlanService


def payload(sku, line_id=None):
    return {"gp_sku": sku, "gp_line_id": line_id, "gp_speed_kg_hour": None, "revision": 0,
            "items": [{"sku": "PF-NEW", "name": "Новый ПФ", "kind": "pf", "line_id": None,
                       "speed_kg_hour": 50, "lead_days": 3, "note": "Настройка планера", "enabled": True}]}


def test_existing_mappings_visible_without_writes(client, db):
    before = db.scalar(select(func.count(PortalSetting.id)))
    response = client.get("/planner-admin/pf-mappings")
    assert response.status_code == 200, response.text
    data = response.json()
    assert len(data["mappings"]) == 55
    assert sum(len(row["items"]) for row in data["mappings"]) == 56
    assert next(row for row in data["mappings"] if row["gp_sku"] == "1010027200")["gp_speed_kg_hour"] == 27.26
    assert any(row["sku"] == "1021002013" for row in data["products"])
    assert db.scalar(select(func.count(PortalSetting.id))) == before


def test_planner_can_edit_replace_and_create_from_saved_mapping(client, db):
    plan, values, line = setup_gp(db)
    admin = db.scalar(select(User).where(User.username == "regular.admin"))
    admin.role = "planner"; db.commit()
    config = payload("1010023231")
    config["items"][0]["line_id"] = line.id
    response = client.put("/planner-admin/pf-mappings", json=config)
    assert response.status_code == 200, response.text
    assert response.json()["revision"] == 1
    assert not productions(plan)
    db.expire_all()
    preview = preview_expansion(db, values)
    assert preview["items"][0]["sku"] == "PF-NEW"
    assert preview["items"][0]["production_date"] == date(2026, 11, 7)
    assert preview["items"][0]["required_hours"] == 2
    PlanService(db).create_manual_task(plan, values)
    assert next(row for row in productions(plan) if row.parent_task_id).product.sku == "PF-NEW"
    assert db.scalar(select(AuditEvent).where(AuditEvent.action == "pf_mapping_updated"))
    config["revision"] = 1
    config["items"][0]["lead_days"] = 5
    assert client.put("/planner-admin/pf-mappings", json=config).status_code == 200
    assert next(row for row in productions(plan) if row.parent_task_id).production_date == date(2026, 11, 7)


def test_line_specific_mapping_overrides_general_only_on_that_line(client, db):
    plan, values, line = setup_gp(db)
    other = db.scalar(select(ProductionLine).where(ProductionLine.name == "Булка"))
    db.add(LineCapability(product_id=values["product_id"], line_id=other.id, units_per_hour=120)); db.commit()
    config = payload("1010023231", other.id)
    config["items"][0]["line_id"] = line.id
    assert client.put("/planner-admin/pf-mappings", json=config).status_code == 200
    assert preview_expansion(db, values)["items"][0]["sku"] == "4510000047"
    assert preview_expansion(db, {**values, "line_id": other.id})["items"][0]["sku"] == "PF-NEW"
    data = client.get("/planner-admin/pf-mappings").json()
    assert len([row for row in data["mappings"] if row["gp_sku"] == "1010023231"]) == 2


def test_disabled_and_empty_mappings_do_not_fall_back_to_source(client, db):
    plan, values, line = setup_gp(db)
    config = payload("1010023231")
    config["items"][0]["enabled"] = False
    assert client.put("/planner-admin/pf-mappings", json=config).status_code == 200
    assert preview_expansion(db, values)["items"] == []
    config.update(revision=1, items=[])
    assert client.put("/planner-admin/pf-mappings", json=config).status_code == 200
    assert preview_expansion(db, values)["items"] == []
    assert next(row for row in client.get("/planner-admin/pf-mappings").json()["mappings"] if row["gp_sku"] == "1010023231")["items"] == []


def test_revision_conflict_and_stale_task_preview(client, db):
    plan, values, line = setup_gp(db)
    before = preview_expansion(db, values)
    config = payload("1010023231")
    config["items"][0]["line_id"] = line.id
    assert client.put("/planner-admin/pf-mappings", json=config).status_code == 200
    assert client.put("/planner-admin/pf-mappings", json=config).status_code == 409
    with pytest.raises(ValueError, match="Связи ГП"):
        PlanService(db).create_manual_task(plan, {**values, "pf_mapping_revision": before["mapping_revision"]})
    assert not productions(plan)


@pytest.mark.parametrize("change", ["self", "duplicate", "negative_days", "unknown_gp", "invalid_line", "invalid_gp_line"])
def test_invalid_mapping_rejected(client, db, change):
    plan, values, line = setup_gp(db)
    config = payload("1010023231")
    if change == "self": config["items"][0]["sku"] = config["gp_sku"]
    if change == "duplicate": config["items"].append(config["items"][0].copy())
    if change == "negative_days": config["items"][0]["lead_days"] = -1
    if change == "unknown_gp": config["gp_sku"] = "NOT-A-GP"
    if change == "invalid_line": config["items"][0]["line_id"] = 999999
    if change == "invalid_gp_line": config["gp_line_id"] = db.scalar(select(ProductionLine.id).where(ProductionLine.name == "Булка"))
    assert client.put("/planner-admin/pf-mappings", json=config).status_code == 422
    assert not db.scalar(select(PortalSetting).where(PortalSetting.key.startswith("pf_mapping:")))


def test_viewer_cannot_read_or_write_mapping_editor(client, db):
    user = db.scalar(select(User).where(User.username == "regular.admin"))
    user.role = "viewer"; db.commit()
    assert client.get("/planner-admin/pf-mappings").status_code == 403
    assert client.put("/planner-admin/pf-mappings", json=payload("1010023231")).status_code == 403


def test_new_gp_and_catalog_speed_fallback(client, db):
    plan, values, line = setup_gp(db, sku="NEW-GP")
    add_pf(db, "PF-NEW", line, speed=80)
    config = payload("NEW-GP")
    config["items"][0].update(line_id=line.id, speed_kg_hour=None)
    response = client.put("/planner-admin/pf-mappings", json=config)
    assert response.status_code == 200
    assert response.json()["items"][0]["name"] == "ПФ"
    assert preview_expansion(db, values)["items"][0]["speed_kg_hour"] == Decimal(80)
    assert preview_expansion(db, values)["gp_speed_kg_hour"] == Decimal(100)
