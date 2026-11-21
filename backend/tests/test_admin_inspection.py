from datetime import datetime, timezone

import pytest
from sqlalchemy import select, func

from test_audit_regressions import db, client
from app.models.entities import AuditEvent, Product, LineCapability, ProductionLine, User


def test_database_tables_and_rows_expose_only_allowed_columns(client, db):
    user = db.scalar(select(User))
    user.password_hash = "do-not-expose"
    db.add(Product(sku="123456", name="Inspection product")); db.commit()
    metadata = client.get("/admin/database").json()
    assert metadata["read_only"]
    assert "portal_settings" not in {t["name"] for t in metadata["tables"]}
    for table in metadata["tables"]:
        result = client.get(f"/admin/database/{table['name']}")
        assert result.status_code == 200, result.text
        assert result.json()["read_only"]
    result = client.get("/admin/database/users")
    assert "password_hash" not in result.text and "do-not-expose" not in result.text
    assert client.get("/admin/database/portal_settings").status_code == 404
    assert client.get("/admin/database/users?sort=password_hash").status_code == 422
    assert client.get("/admin/database/products?column=name);DELETE").status_code == 422
    assert client.post("/admin/database/products", json={"name": "Overwrite"}).status_code == 405
    assert db.scalar(select(Product.name)) == "Inspection product"


def test_database_search_exact_filter_sort_and_pagination(client, db):
    db.add_all([Product(sku=f"SKU-{i}", name="special%" if i == 2 else "regular") for i in range(4)])
    db.commit()
    result = client.get("/admin/database/products?limit=2&sort=sku&direction=asc&offset=1").json()
    assert result["total"] == 4
    assert [r["sku"] for r in result["rows"]] == ["SKU-1", "SKU-2"]
    assert client.get("/admin/database/products?q=%25").json()["total"] == 1  # literal %, not a SQL wildcard
    assert client.get("/admin/database/products?column=sku&value=SKU-3").json()["total"] == 1
    assert client.get("/admin/database/products?q=missing").json()["total"] == 0
    assert client.get("/admin/database/products?limit=101").status_code == 422
    assert client.get("/admin/database/products?offset=-1").status_code == 422


@pytest.mark.parametrize("role", ["planner", "viewer", "master"])
def test_inspection_requires_admin_on_every_endpoint(client, db, role):
    db.scalar(select(User)).role = role; db.commit()
    for url in ("/admin/database", "/admin/database/products", "/admin/audit-log", "/admin/audit-log/filters"):
        assert client.get(url).status_code == 403
    client.cookies.clear()
    assert client.get("/admin/database").status_code == 401


def test_audit_search_resolves_sku_and_redacts_nested_secrets(client, db):
    product = Product(sku="1010035914", name="Pumpkin bread")
    db.add(product); db.flush()
    cap = LineCapability(product_id=product.id, line_id=db.scalar(select(ProductionLine.id)), units_per_hour=78)
    db.add(cap); db.flush()
    for kind, entity_id in (("product", product.id), ("line_capability", cap.id)):
        db.add(AuditEvent(username="test.editor", action="product_updated", entity_type=kind, entity_id=str(entity_id),
                          details={"after": {"units_per_hour": "78", "password": "hidden-value"}, "list": [{"api_token": "hidden-token"}]}))
    db.commit()
    for term in ("1010035914", "Pumpkin"):
        result = client.get(f"/admin/audit-log?q={term}")
        assert result.json()["total"] == 2
        assert "hidden-value" not in result.text and "hidden-token" not in result.text
        assert result.json()["rows"][0]["details"]["after"]["units_per_hour"] == "78"
    assert client.get("/admin/database/audit_events").json()["rows"][0]["details"]["after"]["password"] == "[скрыто]"
    assert client.get("/admin/audit-log?username=someone-else").json()["total"] == 0
    assert client.get("/admin/audit-log?entity_type=product&action=product_updated").json()["total"] == 1
    assert client.get("/admin/audit-log/filters").json() == {"actions": ["product_updated"], "users": ["test.editor"], "entities": ["line_capability", "product"]}


def test_audit_dates_are_moscow_days_and_order_is_stable(client, db):
    for hour in (20, 21, 22):
        db.add(AuditEvent(username="editor", action="test", entity_type="product", details={},
                          created_at=datetime(2026, 11, 10, hour, tzinfo=timezone.utc)))
    db.commit()
    result = client.get("/admin/audit-log?start=2026-11-11&end=2026-11-11&limit=1").json()
    assert result["total"] == 2 and len(result["rows"]) == 1
    second = client.get("/admin/audit-log?start=2026-11-11&end=2026-11-11&limit=1&offset=1").json()
    assert second["rows"][0]["id"] < result["rows"][0]["id"]
    assert client.get("/admin/audit-log?start=2026-11-12&end=2026-11-11").status_code == 422
    assert db.scalar(select(func.count(AuditEvent.id))) == 3
