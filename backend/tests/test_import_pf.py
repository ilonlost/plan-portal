from datetime import date
from decimal import Decimal
from io import BytesIO

import pytest
from openpyxl import Workbook
from sqlalchemy import select

from app.api.routes import imports
from app.models.entities import ProductionPlan, ProductionScheduleItem
from app.services import bom_volume_service
from app.services.plan_service import PlanService
from test_audit_regressions import client, db
from test_pf_expansion import setup_gp, add_pf


@pytest.mark.parametrize("scoped", [False, True])
def test_excel_pf_selection_is_atomic_and_survives_recalculation(client, db, monkeypatch, scoped):
    plan, _, line = setup_gp(db)
    add_pf(db, "4510000047", line, speed=50)
    monkeypatch.setattr(imports, "send_notification", lambda *args: None)
    monkeypatch.setattr(bom_volume_service, "fetch_bom_volume", lambda sku, volume: [
        {"body_nr": "4510000047", "comp_qnt": str(volume * Decimal(".125")), "ek_me": "кг"}
    ])
    book = Workbook()
    sheet = book.active
    sheet.title = "ОХЛ"
    sheet.append(["Артикул", "Наименование", date(2026, 11, 10), date(2026, 11, 11)])
    sheet.append(["1010023231", "ГП", 40, 24])
    sheet.append(["99999999", "Без связей", 5, None])
    data = BytesIO()
    book.save(data)
    query = f"?line_id={line.id}" if scoped else ""
    response = client.post("/imports/preview" + query, files={"file": ("plan.xlsx", data.getvalue())})
    assert response.status_code == 200, response.text
    preview = response.json()
    assert list(preview["pf_matches"]) == ["1010023231"]
    payload = {"preview": preview}
    if scoped:
        payload.update(line_id=line.id, plan_version=preview["plan_version"])
    response = client.post("/imports/confirm", json=payload)
    assert response.status_code == 200, response.text
    offer = response.json()["pf_offer"]
    endpoint = f"/plans/{offer['plan_id']}/imports/{offer['order_id']}/pf"
    headers = {"If-Match": str(offer["version"])}
    assert len(offer["candidates"]) == 2
    assert not list(db.scalars(select(ProductionScheduleItem).where(ProductionScheduleItem.parent_task_id.is_not(None))))

    selections = []
    for candidate in offer["candidates"]:
        parent_id = candidate["task_id"]
        calc = client.get(f"{endpoint}/{parent_id}").json()
        assert Decimal(calc["items"][0]["quantity_kg"]) == Decimal(candidate["quantity_kg"]) / 8
        selections.append(dict(task_id=parent_id, pf_tasks=[{"sku": "4510000047", "speed_kg_hour": 35}],
                               pf_mapping_revision=calc["mapping_revision"], pf_calculation_revision=calc["calculation_revision"]))
    invalid = [selections[0], {**selections[1], "pf_calculation_revision": "0" * 64}]
    assert client.post(endpoint, json=invalid, headers=headers).status_code == 422
    assert len(client.get(endpoint).json()["candidates"]) == 2
    result = client.post(endpoint, json=selections, headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()["created"] == 2
    assert client.post(endpoint, json=selections, headers=headers).status_code == 409
    assert client.get(endpoint).json()["candidates"] == []
    plan = db.get(ProductionPlan, offer["plan_id"])
    service = PlanService(db)
    service.calculate(plan, service._latest_source_demands())
    children = [row for row in plan.schedule_items if row.parent_task_id]
    assert {row.parent_task_id for row in children} == {item["task_id"] for item in selections}
    assert sorted(row.quantity_kg for row in children) == [3, 5]
    assert all(Decimal(row.pf_details["speed_kg_hour"]) == 35 for row in children)
    if scoped:
        payload["plan_version"] = plan.revision
    result = client.post("/imports/confirm", json=payload)
    assert result.status_code == 200, result.text
    assert len(result.json()["pf_offer"]["candidates"]) == 2
    assert all(item["source_kind"] != "pf" for item in result.json()["plan"]["items"])
