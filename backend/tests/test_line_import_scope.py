from datetime import date
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook
from sqlalchemy import select

from test_audit_regressions import db, client, make_plan
from app.api.routes import imports, plans
from app.core.config import settings
from app.core.security import UserContext, create_session_token
from app.models.entities import DemandItem, ImportedOrder, LineCapability, LineCapacity, Product, ProductionLine, ProductionScheduleItem, User
from app.schemas.common import ImportPreview, ImportRow
from app.services.plan_service import PlanService


def two_lines(db, monkeypatch):
    monkeypatch.setattr(imports, "send_notification", lambda *args, **kwargs: None)
    monkeypatch.setattr(plans, "send_notification", lambda *args, **kwargs: None)
    plan, first = make_plan(db)
    target = next(row.line_id for row in plan.schedule_items if row.schedule_kind == "production")
    db.scalar(select(LineCapacity).where(LineCapacity.line_id == target, LineCapacity.shift == "day")).available_hours = 2
    other = db.scalar(select(ProductionLine).where(ProductionLine.workshop_code == "PC"))
    product = Product(sku="202", name="Other product", box_weight_kg=1)
    db.add(product); db.flush()
    db.add(LineCapability(line_id=other.id, product_id=product.id, units_per_hour=100))
    second = DemandItem(order_id=first.order_id, product=product, source_row=4, sku="202", product_name=product.name,
                        quantity=50, source_quantity=50, source_unit="кг", source_kind="ohl", source_plan_date=first.source_plan_date,
                        requested_date=first.requested_date, due_date=first.due_date, exact_date=True)
    db.add(second); db.flush()
    service = PlanService(db); service.calculate(plan, [first, second])
    zam_order = ImportedOrder(source_name="ZAM.xlsx", template_type="quarter_weekly")
    db.add(zam_order); db.flush()
    zam = DemandItem(order_id=zam_order.id, product=first.product, source_row=2, sku=first.sku, product_name="ZAM product",
                     quantity=20, source_quantity=20, source_unit="кг", source_kind="zam", source_plan_date=first.source_plan_date,
                     requested_date=first.requested_date, due_date=first.due_date)
    db.add(zam); db.flush(); service.calculate(plan, [first, second, zam])
    return plan, first, second, target, other.id


def preview(quantity=35, kind="ohl_daily", skus=("101", "202")):
    rows = [ImportRow(row_number=i+2, sku=sku, product_name="Replacement", quantity=quantity, quantity_kg=quantity,
                      source_quantity=quantity, requested_date=date(2026, 9, 8), due_date=date(2026, 9, 8),
                      source_plan_date=date(2026, 9, 8), marking_date=date(2026, 9, 8), exact_date=kind == "ohl_daily")
            for i, sku in enumerate(skus)]
    return ImportPreview(file_name="standard.xlsx", mapping_code="default_v1", template_type=kind,
                         total_rows=len(rows), valid_rows=len(rows), invalid_rows=0, columns=[], rows=rows)


def snapshot(plan, line_id):
    return sorted([(row.id, {col.name: getattr(row, col.name) for col in ProductionScheduleItem.__table__.columns})
                   for row in plan.schedule_items if row.line_id == line_id], key=lambda row: row[0])


def test_line_upload_preserves_other_line_and_zam_and_survives_recalculation(client, db, monkeypatch):
    plan, first, second, target, other = two_lines(db, monkeypatch)
    protected = snapshot(plan, other)
    for amount in (35, 45):
        response = client.post("/imports/confirm", json={"preview": preview(amount).model_dump(mode="json"), "line_id": target, "plan_version": plan.revision})
        assert response.status_code == 200, response.text
        assert snapshot(plan, other) == protected  # IDs, washes, status and rank as well as quantities.
        effective = PlanService(db)._latest_source_demands()
        assert len(effective) == 3
        assert second in effective and first not in effective
        assert sum(row.quantity for row in effective if row.source_kind == "ohl" and row.sku == "101") == amount
        rows = [row for row in plan.schedule_items if not row.excluded and row.schedule_kind == "production"]
        assert sum(row.quantity for row in rows if row.line_id == target and row.source_kind == "ohl") == amount
        assert sum(row.quantity for row in rows if row.source_kind == "zam") == 20
        PlanService(db).calculate(plan, effective)
        assert sum(row.quantity for row in plan.schedule_items if not row.excluded and row.source_kind == "ohl" and row.line_id == target) == amount
        # The next upload protects the state from this point, including the recalculated other line.
        protected = snapshot(plan, other)


def test_bulk_line_delete_preview_version_and_no_resurrection(client, db, monkeypatch):
    plan, first, second, target, other = two_lines(db, monkeypatch)
    protected = snapshot(plan, other)
    inspected = client.get(f"/planner-admin/line-tasks?line_id={target}&start=2026-09-08&end=2026-09-08")
    assert inspected.status_code == 200
    assert inspected.json()["count"] == 2
    payload = {"line_id": target, "start_date": "2026-09-08", "end_date": "2026-09-08"}
    assert client.post(f"/plans/{plan.id}/bulk-delete-items", json=payload, headers={"If-Match": "999"}).status_code == 409
    result = client.post(f"/plans/{plan.id}/bulk-delete-items", json=payload, headers={"If-Match": str(plan.revision)})
    assert result.status_code == 200 and result.json()["deleted_count"] == 2
    assert snapshot(plan, other) == protected
    PlanService(db).calculate(plan, PlanService(db)._latest_source_demands())
    assert not any(not row.excluded and row.schedule_kind == "production" and row.line_id == target for row in plan.schedule_items)
    # A deliberate replacement upload can restore the newly supplied OHL quantity after deletion.
    result = client.post("/imports/confirm", json={"preview": preview(65).model_dump(mode="json"), "line_id": target, "plan_version": plan.revision})
    assert result.status_code == 200, result.text
    assert sum(row.quantity for row in plan.schedule_items if not row.excluded and row.schedule_kind == "production" and row.line_id == target) == 65


def test_scoped_source_deletion_does_not_resurrect_old_upload_and_complete_upload_supersedes_scope(client, db, monkeypatch):
    plan, first, second, target, other = two_lines(db, monkeypatch)
    response = client.post("/imports/confirm", json={"preview": preview().model_dump(mode="json"), "line_id": target, "plan_version": plan.revision})
    assert response.status_code == 200, response.text
    protected = snapshot(plan, other)
    response = client.delete(f"/planner-admin/sources/{response.json()['order_id']}")
    assert response.status_code == 200, response.text
    assert snapshot(plan, other) == protected
    assert [row.sku for row in PlanService(db)._latest_source_demands() if row.source_kind == "ohl"] == ["202"]
    response = client.post("/imports/confirm", json={"preview": preview(80).model_dump(mode="json")})
    assert response.status_code == 200, response.text
    assert sum(row.quantity for row in PlanService(db)._latest_source_demands() if row.source_kind == "ohl") == 160


def test_scoped_import_rejects_stale_version_empty_reference_and_other_line_rows(client, db, monkeypatch):
    plan, first, second, target, other = two_lines(db, monkeypatch)
    orders_before = list(db.scalars(select(ImportedOrder.id)))
    for sample, version in ((preview(), 999), (preview(skus=("202",)), plan.revision), (preview(kind="capacity_reference"), plan.revision)):
        response = client.post("/imports/confirm", json={"preview": sample.model_dump(mode="json"), "line_id": target, "plan_version": version})
        assert response.status_code in (409, 422), response.text
    assert list(db.scalars(select(ImportedOrder.id))) == orders_before
    assert not (first.raw_data or {}).get("replaced_by_line_id")


def test_preview_standard_maintenance_file_and_scoped_replacement_keep_other_events(client, db, monkeypatch):
    plan, first, second, target, other = two_lines(db, monkeypatch)
    target_line = db.get(ProductionLine, target); other_line = db.get(ProductionLine, other)
    plan.schedule_items.append(ProductionScheduleItem(line_id=other, production_date=date(2026, 9, 8), shift="day", quantity=0,
        required_hours=2, duration_hours=2, schedule_kind="maintenance", source="maintenance_import", reason="Other maintenance"))
    db.commit(); protected = snapshot(plan, other)
    book = Workbook(); sheet = book.active; sheet.title = "График ТО"
    sheet.append(["Линия", "Дата", "Смена", "Тип события", "Длительность, ч", "Причина / работы"])
    sheet.append([target_line.name, "08.09.2026", "День", "ТО", 1, "Target"])
    sheet.append([other_line.name, "08.09.2026", "День", "ТО", 3, "Must be skipped"])
    stream = BytesIO(); book.save(stream)
    response = client.post(f"/imports/preview?line_id={target}", files={"file": ("График ТО.xlsx", stream.getvalue())})
    assert response.status_code == 200, response.text
    assert len(response.json()["rows"]) == 1
    result = client.post("/imports/confirm", json={"preview": response.json(), "line_id": target, "plan_version": plan.revision})
    assert result.status_code == 200, result.text
    assert snapshot(plan, other) == protected


def test_viewer_cannot_preview_delete_or_import_line(client, db, monkeypatch):
    plan, first, second, target, other = two_lines(db, monkeypatch)
    db.scalar(select(User).where(User.username == "regular.admin")).role = "viewer"; db.commit()
    client.cookies.set(settings.session_cookie_name, create_session_token(UserContext("regular.admin", "Viewer", "viewer")))
    assert client.get(f"/planner-admin/line-tasks?line_id={target}&start=2026-09-08&end=2026-09-08").status_code == 403
    assert client.post("/imports/confirm", json={"preview": preview().model_dump(mode="json"), "line_id": target, "plan_version": plan.revision}).status_code == 403
    assert client.post(f"/plans/{plan.id}/bulk-delete-items", json={"line_id": target, "start_date": "2026-09-08", "end_date": "2026-09-08"}, headers={"If-Match": str(plan.revision)}).status_code == 403


def test_two_line_overlays_remain_independent_and_split_demand_is_rejected(client, db, monkeypatch):
    plan, first, second, target, other = two_lines(db, monkeypatch)
    for line, sku, amount in ((target, "101", 35), (other, "202", 75)):
        result = client.post("/imports/confirm", json={"preview": preview(amount, skus=(sku,)).model_dump(mode="json"), "line_id": line, "plan_version": plan.revision})
        assert result.status_code == 200, result.text
    effective = PlanService(db)._latest_source_demands()
    assert {(row.sku, row.quantity) for row in effective if row.source_kind == "ohl"} == {("101", Decimal(35)), ("202", Decimal(75))}
    PlanService(db).calculate(plan, effective)
    assert sum(row.quantity for row in plan.schedule_items if not row.excluded and row.source_kind == "ohl") == 110
    demand = next(row for row in effective if row.sku == "101" and row.source_kind == "ohl")
    plan.schedule_items.append(ProductionScheduleItem(demand_item_id=demand.id, product_id=demand.product_id, line_id=other,
        production_date=demand.requested_date, quantity=1, source_kind="ohl", source="manual", schedule_kind="production"))
    db.commit(); protected = snapshot(plan, other)
    response = client.post("/imports/confirm", json={"preview": preview(15).model_dump(mode="json"), "line_id": target, "plan_version": plan.revision})
    assert response.status_code == 409 and "несколькими линиями" in response.json()["detail"]
    assert snapshot(plan, other) == protected
