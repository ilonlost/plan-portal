from datetime import date
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select

from test_audit_regressions import db, client
from test_pf_expansion import setup_gp, add_pf, productions
from app.models.entities import Product, ProductionLine
from app.services import bom_volume_service as bom
from app.services.pf_service import preview_expansion
from app.services.plan_service import PlanService, schedule_item_dict


def bom_response(volume):
    # Observed live response: 324 GP packs -> 1296 croissants and 13.7376 kg filling.
    return [
        {"body_nr": 1021002013, "comp_qnt": "0.000000", "ek_me": "шт", "step_prom": 1},
        {"body_nr": 1021002013, "comp_qnt": str(volume * 4), "ek_me": "шт", "step_prom": 1},
        {"body_nr": 1021001493, "comp_qnt": str(volume * Decimal("0.0416")), "ek_me": "кг", "step_prom": 2},
        {"body_nr": 1021001493, "comp_qnt": str(volume * Decimal("0.0008")), "ek_me": "кг", "step_prom": 2},
        {"body_nr": 1030100324, "comp_qnt": "99", "ek_me": "кг", "step_prom": 3},
    ]


@pytest.fixture
def scenario(db, monkeypatch):
    plan, values, line = setup_gp(db, "1010027200")
    gp = db.get(Product, values["product_id"])
    gp.unit_weight_kg = Decimal("0.32")
    gp.box_weight_kg = Decimal("3.84")
    gp.units_per_box = 12
    pf = add_pf(db, "1021002013", line)
    pf.unit_weight_kg = Decimal("0.08")
    filling_line = db.scalar(select(ProductionLine).where(ProductionLine.name == "НАЧИНКА"))
    add_pf(db, "1021001493", filling_line)
    db.commit()
    calls = []

    def fetch(sku, volume):
        calls.append((sku, volume))
        return bom_response(volume)

    monkeypatch.setattr(bom, "fetch_bom_volume", fetch)
    return plan, values, calls


def test_100kg_preview_and_creation_use_bom_units_and_kg(db, scenario):
    plan, values, calls = scenario
    preview = preview_expansion(db, values)
    assert calls == [("1010027200", Decimal(324))]
    assert preview["quantity_kg"] == Decimal("103.68")
    assert preview["quantity_units"] == 324
    pf, filling = preview["items"]
    assert pf["quantity_units"] == 1296 and pf["quantity_kg"] == Decimal("103.68")
    assert filling["quantity_kg"] == Decimal("13.7376") and filling["quantity_units"] is None
    assert not productions(plan)
    PlanService(db).create_manual_task(plan, {**values, "pf_calculation_revision": preview["calculation_revision"]})
    gp, pf, filling = productions(plan)
    assert pf.quantity_kg == Decimal("103.68")
    assert filling.quantity_kg == Decimal("13.7376")
    assert schedule_item_dict(pf)["quantity_units"] == 1296
    assert schedule_item_dict(gp)["quantity_units"] == 324
    assert pf.source_unit == "шт" and pf.source_quantity == 1296
    assert filling.required_hours == PlanService._production_duration(Decimal("13.7376"), Decimal(100))
    PlanService(db).calculate(plan, [])
    db.expire_all()
    assert filling.quantity_kg == Decimal("13.7376")
    assert schedule_item_dict(pf)["quantity_units"] == 1296


def test_parent_volume_edit_requeries_bom_and_date_edit_does_not(db, scenario):
    plan, values, calls = scenario
    service = PlanService(db)
    service.create_manual_task(plan, values)
    gp, pf, filling = productions(plan)
    service.update_item(gp, {"quantity": Decimal(200)})
    assert calls[-1] == ("1010027200", Decimal(636))
    assert pf.source_quantity == 2544 and pf.quantity_kg == Decimal("203.52")
    assert filling.quantity_kg == Decimal("26.9664")
    calls.clear()
    service.update_item(gp, {"production_date": date(2026, 11, 12)})
    assert not calls and filling.production_date == date(2026, 11, 7)


def test_stale_bom_preview_rejected_before_writes(db, scenario, monkeypatch):
    plan, values, _ = scenario
    preview = preview_expansion(db, values)
    monkeypatch.setattr(bom, "fetch_bom_volume", lambda sku, volume: bom_response(volume * 2))
    with pytest.raises(ValueError, match="BOM изменился"):
        PlanService(db).create_manual_task(plan, {**values, "pf_calculation_revision": preview["calculation_revision"]})
    assert not productions(plan)


def test_missing_component_blocks_whole_creation_but_checkbox_off_works(db, scenario, monkeypatch):
    plan, values, _ = scenario
    monkeypatch.setattr(bom, "fetch_bom_volume", lambda *args: [])
    with pytest.raises(ValueError, match="отсутствует"):
        PlanService(db).create_manual_task(plan, values)
    assert not productions(plan)
    PlanService(db).create_manual_task(plan, {**values, "expand_pf": False})
    assert len(productions(plan)) == 1


def test_bom_api_error_returns_actionable_422(client, db, scenario, monkeypatch):
    plan, values, _ = scenario
    def fail(*args):
        raise ValueError("Не удалось получить расчёт ПФ из BOM")
    monkeypatch.setattr(bom, "fetch_bom_volume", fail)
    values.update(production_date="2026-11-10", quantity=100)
    response = client.post(f"/plans/{plan.id}/pf-preview", json=values)
    assert response.status_code == 422 and "BOM" in response.json()["detail"]
    assert not productions(plan)


def test_fetch_uses_volume_endpoint_without_basis_scaling(monkeypatch):
    def get(url, **kwargs):
        assert url.endswith("/api/bom-procedure-volume/1010027200")
        assert kwargs["params"] == {"volume": "324"}
        return httpx.Response(200, json={"bom_item": "1010027200", "items": bom_response(Decimal(324))}, request=httpx.Request("GET", url))
    monkeypatch.setattr(bom.httpx, "get", get)
    assert len(bom.fetch_bom_volume("1010027200", Decimal(324))) == 5


@pytest.mark.parametrize("failure", ["timeout", "http", "json", "wrong_sku"])
def test_fetch_failures_are_validation_errors(monkeypatch, failure):
    def get(url, **kwargs):
        if failure == "timeout":
            raise httpx.ReadTimeout("timeout")
        request = httpx.Request("GET", url)
        if failure == "http":
            return httpx.Response(503, request=request)
        if failure == "json":
            return httpx.Response(200, text="<html>login</html>", request=request)
        return httpx.Response(200, json={"bom_item": "wrong", "items": []}, request=request)
    monkeypatch.setattr(bom.httpx, "get", get)
    with pytest.raises(ValueError, match="BOM"):
        bom.fetch_bom_volume("1010027200", Decimal(324))


@pytest.mark.parametrize("quantity,unit", [("NaN", "кг"), ("Infinity", "кг"), ("-1", "кг"), (None, "кг"), ("1", "л"), ("0", "кг")])
def test_invalid_component_amounts_rejected(quantity, unit):
    with pytest.raises(ValueError):
        bom.component_volumes([{"body_nr": "PF", "comp_qnt": quantity, "ek_me": unit}], {"PF": None})


def test_weight_required_for_pieces_and_box_weight_fallback():
    product = Product(sku="PF", box_weight_kg=Decimal(4), units_per_box=50)
    assert bom.unit_weight(product) == Decimal("0.08")
    assert bom.gp_units(product, Decimal(100)) == 1250
    with pytest.raises(ValueError, match="вес штуки"):
        bom.gp_units(Product(sku="GP"), Decimal(100))
    with pytest.raises(ValueError, match="вес штуки"):
        bom.component_volumes([{"body_nr": "PF", "comp_qnt": "10", "ek_me": "шт"}], {"PF": None})
