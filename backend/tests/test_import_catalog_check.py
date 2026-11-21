from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from test_audit_regressions import db, client, make_plan
from app.api.routes import imports
from app.models.entities import Product, ProductionLine, LineCapability
from app.schemas.common import ImportPreview, ImportRow
from app.services.import_catalog_check import check_import_catalog
from app.services.plan_service import PlanService


def sample(sku="unknown", hint="Миквак", kind="quarter_weekly"):
    return ImportPreview(file_name="plan.xlsx", mapping_code="default", template_type=kind, total_rows=1, valid_rows=1, invalid_rows=0, columns=[],
        rows=[ImportRow(row_number=2, sku=sku, product_name="Test product", line_hint=hint, quantity=100, source_quantity=100,
                        requested_date=date(2026, 11, 9), due_date=date(2026, 11, 15))])


@pytest.mark.parametrize("kind", ["ohl_daily", "quarter_weekly"])
@pytest.mark.parametrize("state,expected", [("missing", "артикул отсутствует"), ("unmapped", "не назначена"), ("inactive", "неактивным"), ("zero", "скорость"), ("blocked", "заблокирован")])
def test_preview_checks_actual_catalog_even_with_recognized_line_hint(client, db, monkeypatch, state, expected, kind):
    line = db.scalar(select(ProductionLine).where(ProductionLine.name == "Миквак"))
    if state != "missing":
        product = Product(sku="unknown", name="test", catalog_status="blocked" if state == "blocked" else "active")
        db.add(product); db.flush()
        if state in {"inactive", "zero"}:
            if state == "inactive": line.status = "inactive"
            db.add(LineCapability(product_id=product.id, line_id=line.id, units_per_hour=0 if state == "zero" else 100))
        db.commit()
    monkeypatch.setattr(imports.ExcelImportService, "parse", lambda *args: sample(kind=kind))
    result = client.post("/imports/preview", files={"file": ("plan.xlsx", b"fake")})
    assert result.status_code == 200, result.text
    body = result.json()
    assert expected in body["catalog_issues"][0]["reason"]
    assert body["rows"][0]["warnings"][0].startswith("Справочник: ")
    assert body["valid_rows"] == 1  # informational, preserve the usual import behavior


def test_usable_mapping_ignores_hint_and_inactive_legacy_mapping(db):
    product = Product(sku="known", name="test"); db.add(product); db.flush()
    lines = list(db.scalars(select(ProductionLine)))[:2]
    lines[0].status = "inactive"
    db.add_all([LineCapability(product_id=product.id, line_id=line.id, units_per_hour=100) for line in lines]); db.commit()
    preview = sample("known", hint="Different name")
    check_import_catalog(db, preview)
    assert not preview.catalog_issues
    check_import_catalog(db, preview, lines[0].id)
    assert "неактивным" in preview.catalog_issues[0]["reason"]


def test_issues_group_repeated_dates_and_disappear_after_catalog_fix(db):
    preview = sample()
    preview.rows.append(preview.rows[0].model_copy(update={"row_number": 3}))
    check_import_catalog(db, preview)
    assert preview.catalog_issues[0]["row_numbers"] == [2, 3]
    product = Product(sku="unknown", name="test"); db.add(product); db.flush()
    db.add(LineCapability(product_id=product.id, line_id=db.scalar(select(ProductionLine.id)), units_per_hour=100)); db.commit()
    check_import_catalog(db, preview)
    assert not preview.catalog_issues and not preview.rows[0].warnings


def test_fixed_catalog_does_not_persist_preview_warning_in_recalculated_plan(db):
    plan, demand = make_plan(db)
    demand.validation_errors = ["Справочник: не назначена производственная линия и скорость"]
    PlanService(db).calculate(plan, [demand])
    assert not demand.validation_errors
    assert not any(w.startswith("Справочник:") for row in plan.schedule_items for w in row.warnings)


def test_scoped_preview_reports_unmapped_skus_in_error(client, db, monkeypatch):
    line = db.scalar(select(ProductionLine).where(ProductionLine.name == "Миквак"))
    monkeypatch.setattr(imports.ExcelImportService, "parse", lambda *args: sample())
    response = client.post(f"/imports/preview?line_id={line.id}", files={"file": ("plan.xlsx", b"fake")})
    assert response.status_code == 422
    assert "unknown" in response.json()["detail"] and "артикул отсутствует" in response.json()["detail"]


def test_scoped_preview_keeps_diagnostic_for_excluded_unmapped_row(client, db, monkeypatch):
    plan, demand = make_plan(db)
    line = demand.product.capabilities[0].line
    preview = sample(demand.sku, hint=line.name)
    preview.rows.append(sample("unknown", hint=line.name).rows[0].model_copy(update={"row_number": 3}))
    preview.total_rows = preview.valid_rows = 2
    monkeypatch.setattr(imports.ExcelImportService, "parse", lambda *args: preview)
    response = client.post(f"/imports/preview?line_id={line.id}", files={"file": ("plan.xlsx", b"fake")})
    assert response.status_code == 200, response.text
    result = response.json()
    assert [r["sku"] for r in result["rows"]] == [demand.sku]
    assert result["catalog_issues"][0]["sku"] == "unknown"
    assert "не отобраны" in result["catalog_issues"][0]["reason"]
