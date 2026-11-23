from io import BytesIO
from unittest.mock import Mock

from openpyxl import load_workbook
from sqlalchemy import select

from app.api.routes import catalog
from app.models.entities import AuditEvent, LineCapability, Product, ProductionLine, User
from tests.test_audit_regressions import client, db


def test_selection_actions(client, db, monkeypatch):
    products = [Product(sku=sku, name=sku, advance_status="АЗ") for sku in ("001", "002", "003")]
    db.add_all(products)
    db.flush()
    a, b, outside = products
    first, second = db.scalars(select(ProductionLine).order_by(ProductionLine.id).limit(2)).all()
    db.add_all([LineCapability(product_id=p.id, line_id=first.id, units_per_hour=10) for p in products])
    db.add(LineCapability(product_id=b.id, line_id=second.id, units_per_hour=80))
    db.commit()
    recalc = Mock(return_value=True)
    monkeypatch.setattr(catalog, "_recalculate_active_plan", recalc)
    monkeypatch.setattr(catalog, "send_notification", Mock())
    selection = {"product_ids": [a.id, b.id, b.id]}
    def update(**change):
        result = client.post("/catalog/selection/update", json={**selection, "change": change})
        assert result.status_code == 200, result.text
        return result.json()["changed"]

    assert update(action="add_line", line_id=second.id, units_per_hour=30) == 1
    assert update(action="add_line", line_id=second.id, units_per_hour=70) == 0
    assert recalc.call_count == 1
    assert update(action="set_status", status="blocked") == 2
    assert not a.active and b.fk_status == "Блокирован"
    assert update(action="set_status", status="active") == 2
    assert a.active and b.fk_status == "Активный"
    assert update(action="set_date_status", advance_status="По графику") == 2
    assert a.advance_status == b.advance_status == "По графику" and outside.advance_status == "АЗ"
    stale = {"product_ids": [a.id, 999999], "change": {"action": "set_date_status", "advance_status": "АЗ"}}
    assert client.post("/catalog/selection/update", json=stale).status_code == 409
    assert a.advance_status == "По графику"
    assert update(action="set_date_status", advance_status="АЗ") == 2
    assert recalc.call_count == 5
    assert len(db.scalars(select(AuditEvent).where(AuditEvent.action.like("catalog_bulk_%"))).all()) == 9
    assert outside.active and outside.fk_status is None

    response = client.post("/catalog/selection/export.xlsx", json=selection)
    assert response.status_code == 200
    book = load_workbook(BytesIO(response.content), read_only=True)
    assert [row[1] for row in list(book["Артикулы"].values)[1:]] == ["001", "002"]
    assert {(row[1], row[2], row[5]) for row in list(book["Параметры SKU"].values)[1:]} == {
        ("001", first.code, 10), ("002", first.code, 10), ("001", second.code, 30), ("002", second.code, 80),
    }
    assert client.post("/catalog/selection/export.xlsx", json={"product_ids": []}).status_code == 422
    assert client.post("/catalog/selection/export.xlsx", json={"product_ids": [999999]}).status_code == 409
    assert client.post("/catalog/selection/update", json={**selection, "change": {"action": "set_date_status", "advance_status": "План"}}).status_code == 422
    admin = db.scalar(select(User).where(User.username == "regular.admin"))
    admin.role = "viewer"
    db.commit()
    assert client.post("/catalog/selection/export.xlsx", json=selection).status_code == 403
    assert client.post("/catalog/selection/update", json=stale).status_code == 403
