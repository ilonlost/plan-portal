from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path
import asyncio

import pytest
from fastapi import FastAPI, UploadFile
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm.exc import StaleDataError

from app.db.base import Base
from app.db.session import get_db
from app.core.config import settings
from app.core.security import UserContext, create_session_token, parse_session_token
from app.api.routes import session, plans, imports, catalog, admin
from app.models.entities import User, Product, ProductionLine, ProductionPlan, ProductionPlanVersion, ProductionScheduleItem, LineCapability, LineCapacity, DemandItem, ImportedOrder
from app.schemas.common import ImportConfirmRequest
from app.services.import_service import ExcelImportService
from app.services.plan_service import PlanService
from app.services.planning_engine import PlanningEngine, DemandInput, CapabilityInput, CapacityInput
from app.services.notification_service import build_plan_email_html
from app.seed import WORKSHOPS


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as session_db:
        for code, (name, lines) in WORKSHOPS.items():
            for line in lines:
                session_db.add(ProductionLine(code=line, name=line, workshop_code=code, workshop_name=name, working_hours=22, schedule_code="day_night_daily"))
        session_db.add(User(username="regular.admin", display_name="Administrator", role="admin"))
        session_db.commit()
        yield session_db
    engine.dispose()


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "auth_mode", "ldap")
    monkeypatch.setattr(settings, "session_secret", "test-secret-with-more-than-32-characters")
    app = FastAPI()
    app.include_router(session.router)
    app.include_router(plans.router)
    app.include_router(imports.router)
    app.include_router(catalog.router)
    app.include_router(admin.router)
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    client.cookies.set(settings.session_cookie_name, create_session_token(UserContext("regular.admin", "Administrator", "admin")))
    return client


def test_production_rejects_demo_and_header_bypass(client, db, monkeypatch):
    client.cookies.clear()
    db.add(User(username="demo.admin", display_name="Legacy demo", role="admin", active=True))
    db.commit()
    for mode in ("ldap", "mock", "typo"):
        monkeypatch.setattr(settings, "auth_mode", mode)
        assert client.post("/session/login", json={"username": "demo.admin", "password": "demo"}).status_code == 401
        assert client.get("/session/me", headers={"X-User": "demo.admin"}).status_code == 401
        assert client.get("/session/mode").json() == {"auth_mode": "ldap"}
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(settings, "auth_mode", "local")
    token = create_session_token(UserContext("demo.admin", "Demo", "admin"))
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "auth_mode", "ldap")
    assert parse_session_token(token) is None
    # Ordinary LDAP sessions remain usable and roles are read from the database.
    token = create_session_token(UserContext("regular.admin", "Administrator", "viewer"))
    client.cookies.set(settings.session_cookie_name, token)
    assert client.get("/session/me").json()["role"] == "admin"
    db.scalar(select(User).where(User.username == "regular.admin")).active = False
    db.commit()
    assert client.get("/session/me").status_code == 403


def make_plan(db, quantity=100):
    day = date(2026, 9, 8)
    line = db.scalar(select(ProductionLine).where(ProductionLine.name == "Сэндвичи"))
    product = Product(sku="101", name="Test", box_weight_kg=Decimal("1"))
    order = ImportedOrder(source_name="ОХЛ.xlsx", template_type="ohl_daily")
    db.add_all([product, order]); db.flush()
    db.add(LineCapability(line_id=line.id, product_id=product.id, units_per_hour=100))
    db.add(LineCapacity(line_id=line.id, capacity_date=day, shift="day", available_hours=1, manual_override=True))
    db.add(LineCapacity(line_id=line.id, capacity_date=day, shift="night", available_hours=0, manual_override=True))
    demand = DemandItem(order_id=order.id, product=product, source_row=3, sku="101", product_name="Test", quantity=quantity, source_kind="ohl", source_plan_date=day, source_quantity=quantity, source_unit="кг", requested_date=day, due_date=day, exact_date=True)
    plan = ProductionPlan(name="Комплексный план ФК · ОХЛ + ЗАМ", horizon_start=day, horizon_end=day)
    db.add_all([demand, plan]); db.flush()
    PlanService(db).calculate(plan, [demand])
    return plan, demand


def test_stale_edit_and_missing_version_do_not_write(client, db):
    plan, _ = make_plan(db)
    item = next(i for i in plan.schedule_items if i.schedule_kind == "production")
    version = plan.revision
    path = f"/plans/{plan.id}/items/{item.id}"
    assert client.patch(path, json={"quantity": 90}).status_code == 428
    first = client.patch(path, json={"quantity": 90}, headers={"If-Match": str(version)})
    assert first.status_code == 200, first.text
    assert client.patch(path, json={"quantity": 80}, headers={"If-Match": str(version)}).status_code == 409
    assert item.quantity == 90


def test_database_revision_prevents_simultaneous_writers(db):
    plan, _ = make_plan(db)
    with Session(db.bind) as other:
        stale = other.get(ProductionPlan, plan.id)
        plan.name = "First writer"; db.commit()
        stale.name = "Second writer"
        with pytest.raises(StaleDataError):
            other.commit()


def test_product_soft_delete_keeps_plan_and_catalog_relations(client, db):
    plan, demand = make_plan(db)
    item_ids = [item.id for item in plan.schedule_items]
    product_id = demand.product_id
    response = client.delete(f"/catalog/products/{product_id}")
    assert response.status_code == 200, response.text
    assert db.get(Product, product_id).catalog_status == "deleted"
    assert db.get(Product, product_id).active is False
    assert db.get(ProductionPlan, plan.id) is not None
    assert [item.id for item in db.scalars(select(ProductionScheduleItem).where(ProductionScheduleItem.plan_id == plan.id))] == item_ids
    assert client.get("/catalog").json()["products"] == []


def test_catalog_xlsx_roundtrip_updates_without_deleting_missing_rows(client, db):
    plan, demand = make_plan(db)
    exported = client.get("/catalog/export.xlsx")
    assert exported.status_code == 200
    workbook = load_workbook(BytesIO(exported.content))
    sheet = workbook["Артикулы"]
    sheet.cell(2, 3).value = "Обновлённое название"
    sheet.cell(2, 4).value = "ЗАМ"
    sheet.cell(2, 10).value = "blocked"
    payload = BytesIO(); workbook.save(payload)
    response = client.post("/catalog/import.xlsx", files={"file": ("catalog.xlsx", payload.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert response.status_code == 200, response.text
    product = db.get(Product, demand.product_id)
    assert product.name == "Обновлённое название"
    assert product.state == "ЗАМ"
    assert product.catalog_status == "blocked"
    assert db.get(ProductionPlan, plan.id) is not None


def test_bom_request_uses_sku_and_marks_1000_unit_basis(client, db, monkeypatch):
    _, demand = make_plan(db)
    requested = []

    class FakeResponse:
        def raise_for_status(self): pass
        def json(self): return {"components": [{"material": "Тесто", "quantity": 250}, {"material": "Соус", "quantity": 20}]}

    class FakeClient:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, url, **kwargs): requested.append(url); return FakeResponse()

    monkeypatch.setattr(catalog.httpx, "Client", FakeClient)
    response = client.get(f"/catalog/products/{demand.product_id}/bom")
    assert response.status_code == 200, response.text
    body = response.json()
    assert requested[0].endswith("/101")
    assert body["basis_units"] == 1000
    assert body["columns"] == ["material", "quantity"]
    assert len(body["rows"]) == 2


def test_admin_can_delete_other_user_without_deleting_plan_history(client, db):
    target = User(username="former.user", display_name="Former", role="planner", active=True)
    db.add(target); db.flush()
    day = date(2026, 9, 8)
    plan = ProductionPlan(name="Retained", horizon_start=day, horizon_end=day, created_by_id=target.id)
    db.add(plan); db.flush()
    version = ProductionPlanVersion(plan_id=plan.id, version_number=1, change_type="test", snapshot={}, changed_by_id=target.id)
    db.add(version); db.commit()
    response = client.delete(f"/admin/users/{target.id}")
    assert response.status_code == 200, response.text
    assert db.get(User, target.id) is None
    assert db.get(ProductionPlan, plan.id).created_by_id is None
    assert db.get(ProductionPlanVersion, version.id).changed_by_id is None
    regular = db.scalar(select(User).where(User.username == "regular.admin"))
    assert client.delete(f"/admin/users/{regular.id}").status_code == 409


def test_delete_split_stays_deleted_and_other_volume_survives(db):
    plan, demand = make_plan(db, 150)
    service = PlanService(db)
    original = next(i for i in plan.schedule_items if i.production_date)
    service.delete_item(original)
    service.calculate(plan, [demand])
    live = [i for i in plan.schedule_items if not i.excluded and i.schedule_kind == "production"]
    assert sum(i.quantity for i in live) == 50
    assert original.excluded
    # Reimport under a different filename and row number uses the same business identity.
    new_order = ImportedOrder(source_name="Новая неделя.xlsx", template_type="ohl_daily")
    db.add(new_order); db.flush()
    fresh = DemandItem(order_id=new_order.id, product=demand.product, source_row=9, sku=demand.sku, product_name=demand.product_name, quantity=150, source_kind="ohl", source_plan_date=demand.source_plan_date, requested_date=demand.requested_date, due_date=demand.due_date, exact_date=True)
    db.add(fresh); db.flush()
    assert [d.id for d in service._latest_source_demands()] == [fresh.id]
    service.calculate(plan, [fresh])
    assert sum(i.quantity for i in plan.schedule_items if not i.excluded and i.schedule_kind == "production") == 50


def test_zam_residual_and_increased_capacity():
    day = date(2026, 9, 8)
    demands = [DemandInput(1, 1, "OHL", Decimal("28.4"), day, day, source_kind="ohl", exact_date=True, box_quantum_kg=Decimal("0.6")), DemandInput(2, 2, "ZAM", Decimal("90"), day, day, source_kind="zam")]
    capabilities = [CapabilityInput(1, 1, Decimal("100")), CapabilityInput(1, 2, Decimal("100"))]
    def run(hours):
        result = PlanningEngine().plan(demands, capabilities, [CapacityInput(1, day, Decimal(hours))], day)
        assert sum(i.required_hours for i in result if i.production_date) <= Decimal(hours)
        assert result[0].quantity == Decimal("28.8")
        return result
    assert any(i.status == "unscheduled" for i in run("1"))
    assert not any(i.status == "unscheduled" for i in run("2"))


def test_catalog_speed_recalculates_without_changing_shift_capacity(client, db):
    plan, demand = make_plan(db, 150)
    capability = db.scalar(select(LineCapability).where(LineCapability.product_id == demand.product_id))
    before = {slot.id: slot.available_hours for slot in db.scalars(select(LineCapacity))}
    response = client.patch(f"/catalog/capabilities/{capability.id}", json={"units_per_hour": 200})
    assert response.status_code == 200, response.text
    assert {slot.id: slot.available_hours for slot in db.scalars(select(LineCapacity))} == before
    assert not any(i.status.value == "unscheduled" for i in plan.schedule_items if not i.excluded)


def test_advance_date_and_production_day_do_not_shift_twice(db):
    plan, demand = make_plan(db)
    demand.marking_date = demand.source_plan_date
    demand.product.advance_status = "АЗ"
    line = next(i.line for i in plan.schedule_items if i.line)
    line.production_day_start_hour = 15
    PlanService(db).calculate(plan, [demand])
    assert demand.requested_date == date(2026, 9, 7)
    assert demand.marking_date == date(2026, 9, 8)
    demand.product.advance_status = "По графику"
    PlanService(db).calculate(plan, [demand])
    assert demand.requested_date == date(2026, 9, 8)


def test_shifted_headers_week_and_invalid_amount():
    book = Workbook(); sheet = book.active; sheet.title = "План ЗАМ"
    sheet.append(["2027", None, None, None]); sheet.append([])
    sheet.append(["Название", "47w", "Код товара SAP", "Комментарий"])
    sheet.append(["Продукция", 42, 101, "x"])
    sheet.append(["Ошибка", "#REF!", 102, "x"])
    stream = BytesIO(); book.save(stream)
    result = ExcelImportService().parse(stream.getvalue(), "ЗАМ.xlsx")
    assert result.rows[0].requested_date == date.fromisocalendar(2027, 47, 1)
    assert result.rows[0].quantity == 42
    assert result.invalid_rows == 1


def test_manual_quantity_override_is_not_recreated_on_recalculation(db):
    plan, demand = make_plan(db)
    service = PlanService(db)
    item = next(i for i in plan.schedule_items if i.schedule_kind == "production")
    service.update_item(item, {"quantity": 90})
    service.calculate(plan, [demand])
    assert sum(i.quantity for i in plan.schedule_items if not i.excluded and i.schedule_kind == "production") == 90


def test_production_and_marking_dates_can_be_edited_independently(client, db):
    plan, _ = make_plan(db)
    item = next(i for i in plan.schedule_items if i.schedule_kind == "production")
    original_production_date = item.production_date
    marking_date = original_production_date + timedelta(days=2)
    response = client.patch(
        f"/plans/{plan.id}/items/{item.id}",
        json={"marking_date": marking_date.isoformat()},
        headers={"If-Match": str(plan.revision)},
    )
    assert response.status_code == 200, response.text
    assert item.production_date == original_production_date
    assert item.marking_date == marking_date

    response = client.patch(
        f"/plans/{plan.id}/items/{item.id}",
        json={"production_date": (original_production_date + timedelta(days=1)).isoformat()},
        headers={"If-Match": str(plan.revision)},
    )
    assert response.status_code == 200, response.text
    assert item.production_date == original_production_date + timedelta(days=1)
    assert item.marking_date == marking_date


def test_unknown_reference_line_is_rejected_without_inserting_it(client, db):
    product = Product(sku="unknown-line", name="Test"); db.add(product); db.commit()
    from app.schemas.common import ImportRow
    with pytest.raises(Exception) as caught:
        imports._upsert_capability(db, product, ImportRow(row_number=1, sku=product.sku, line_hint="Новая случайная линия", speed_kg_hour=100))
    assert caught.value.status_code == 422
    assert db.scalar(select(func.count(ProductionLine.id))) == 15


def test_department_templates_do_not_leak_production_fields():
    day = date(2026, 9, 8)
    items = [{"sku": "101", "product_name": "<Test>", "production_date": day, "marking_date": day + timedelta(days=1), "line_name": "PRIVATE-LINE", "required_hours": 2, "quantity_kg": 40, "box_count": 10, "shift": "night", "status": "planned"}]
    warehouse = build_plan_email_html({}, "Plan", day, day, items, "warehouse")
    materials = build_plan_email_html({}, "Plan", day, day, items, "materials")
    production = build_plan_email_html({}, "Plan", day, day, items)
    assert "PRIVATE-LINE" not in warehouse and "09.09.2026" in warehouse
    assert "&lt;Test&gt;" in warehouse
    assert "не заявка на сырьё" in materials
    assert "PRIVATE-LINE" in production


@pytest.mark.parametrize("name,kind", [("ОХЛ.xlsx", "ohl_daily"), ("ЗАМ.xlsx", "quarter_weekly"), ("Мощности для заливки на портал!!!!!!! 08.09.2026.xlsm", "production_reference")])
def test_supplied_workbooks_reconcile(name, kind):
    path = Path.home() / "Downloads" / name
    if not path.exists():
        pytest.skip("Real workbook is local and intentionally not committed")
    preview = ExcelImportService().parse(path.read_bytes(), name)
    assert preview.template_type == kind
    workbook = load_workbook(path, read_only=True, data_only=True)
    if kind == "ohl_daily":
        sheet = workbook["ОХЛ"]
        headers = next(sheet.iter_rows(min_row=2, max_row=2, values_only=True))
        cols = [i for i, value in enumerate(headers) if ExcelImportService._header_date(value, 2026)]
        expected = [(r, i, Decimal(str(values[i]))) for r, values in enumerate(sheet.iter_rows(min_row=3, values_only=True), 3) for i in cols if isinstance(values[i], (float, int)) and values[i] > 0 and values[4] and values[5]]
        assert len(preview.rows) == len(expected) == 802
        assert sum(row.source_quantity for row in preview.rows) == sum(v for _, _, v in expected)
        assert {row.production_week for row in preview.rows} == {36, 37, 38}
        assert preview.reference_rows
    elif kind == "quarter_weekly":
        sheet = workbook["План ЗАМ"]
        expected = [Decimal(str(values[i])) for values in sheet.iter_rows(min_row=3, values_only=True) for i in range(4, 8) if isinstance(values[i], (float, int)) and values[i] > 0 and values[1] and values[2]]
        assert len(preview.rows) == len(expected) == 129
        assert preview.valid_rows == 127
        assert preview.invalid_rows == 2
        assert sum(row.source_quantity for row in preview.rows) == sum(expected)
        assert {row.production_week for row in preview.rows} == {36, 37, 38, 39}
    else:
        first = preview.rows[0]
        assert first.speed_kg_hour == Decimal("288.8")
        assert first.batch_quantum_kg == Decimal("146.52888847267886")
        assert first.min_order_kg == Decimal("439.58666541803655")
    workbook.close()


def test_real_import_pipeline_preserves_capacity_and_status(db, monkeypatch):
    root = Path.home() / "Downloads"
    names = ["Мощности для заливки на портал!!!!!!! 08.09.2026.xlsm", "ОХЛ.xlsx", "ЗАМ.xlsx"]
    if not all((root / name).exists() for name in names):
        pytest.skip("Real workbooks not present")
    monkeypatch.setattr(imports, "send_notification", lambda *args, **kwargs: None)
    user = UserContext("regular.admin", "Administrator", "admin")
    snapshots = []
    for name in names:
        preview = asyncio.run(imports.preview_import(UploadFile(filename=name, file=BytesIO((root / name).read_bytes())), user, db))
        result = imports.confirm_import(ImportConfirmRequest(preview=preview), db, user)
        snapshots.append({c.id: (c.units_per_hour, c.batch_quantum_kg, c.min_order_kg) for c in db.scalars(select(LineCapability))})
    assert snapshots[0] == snapshots[1] == snapshots[2]
    assert db.scalar(select(func.count(ProductionLine.id))) == 15
    service = PlanService(db)
    plan = service.active_plan()
    assert plan and any(item.status.value == "unscheduled" for item in plan.schedule_items)
    usage = defaultdict(lambda: Decimal("0"))
    for item in plan.schedule_items:
        if item.production_date and not item.excluded:
            usage[(item.line_id, item.production_date, item.shift)] += item.required_hours
    capacity = {(c.line_id, c.capacity_date, c.shift): c.available_hours if c.available else 0 for c in db.scalars(select(LineCapacity))}
    assert all(hours <= capacity.get(key, 0) for key, hours in usage.items())
    expected = len([item for item in plan.schedule_items if not item.excluded])
    service.calculate(plan, service._latest_source_demands())
    assert len([item for item in plan.schedule_items if not item.excluded]) == expected
    product = db.scalar(select(Product).where(Product.advance_status == "АЗ"))
    assert product
    product.advance_status = "По графику"; product.fk_status = "Изменено вручную"; db.commit()
    preview = ExcelImportService().parse((root / "ОХЛ.xlsx").read_bytes(), "Повтор ОХЛ.xlsx")
    imports.confirm_import(ImportConfirmRequest(preview=preview), db, user)
    assert product.advance_status == "По графику"
    assert product.fk_status == "Изменено вручную"
    assert len(service._latest_source_demands()) == 802 + 127
