from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.routes.integrations import router
from app.core.security import UserContext, require_planner
from app.db.base import Base
from app.db.session import get_db
from app.models.entities import IntegrationRun, Product, ProductionLine, ProductionPlan, ProductionPlanVersion, ProductionScheduleItem
from app.services.csb_export_service import build_csb_text


@pytest.fixture
def csb_context():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False, autoflush=False) as db:
        day = date(2026, 9, 22)
        plan = ProductionPlan(name='Test plan', horizon_start=day, horizon_end=day + timedelta(days=1))
        line = ProductionLine(code='5410', name='Line', csb_line_code='5410')
        no_code = ProductionLine(code='unknown', name='Missing code')
        product = Product(sku='101001', name='Product', unit_weight_kg=Decimal('0.25'))
        db.add_all([plan, line, no_code, product]); db.flush()
        def item(**values):
            defaults = dict(plan=plan, line=line, product=product, production_date=day, quantity=10,
                            quantity_kg=10, required_hours=Decimal('1.5'), sequence=1)
            defaults.update(values)
            row = ProductionScheduleItem(**defaults); db.add(row)
            return row
        rows = [item(), item(execution_status='in_progress'), item(execution_status='completed'),
                item(excluded=True), item(schedule_kind='cleaning'), item(line=no_code),
                item(production_date=day + timedelta(days=1))]
        db.commit()
        app = FastAPI(); app.include_router(router)
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[require_planner] = lambda: UserContext('planner', 'Planner', 'planner')
        with TestClient(app) as client:
            yield db, client, rows, plan
    engine.dispose()


def test_csb_duration_and_explicit_line_override(csb_context):
    db, client, rows, plan = csb_context
    content, ids = build_csb_text(rows)
    assert ':T55+2:L8+20260922' in content
    assert 'PROD-ORDER:L1+10:T1+5410:' in content
    assert rows[3].id not in ids and rows[4].id not in ids and rows[5].id not in ids
    rows[0].line.csb_t55 = ' 2.25 '
    assert ':T55+3:' in build_csb_text([rows[0]])[0]
    rows[0].line.csb_t55 = ' '
    rows[0].required_hours = Decimal('0.02')
    assert ':T55+1:' in build_csb_text([rows[0]])[0]


def test_download_marks_only_exported_not_started_tasks_and_is_repeatable(csb_context):
    db, client, rows, plan = csb_context
    before = plan.revision
    response = client.post('/integrations/csb/download?start_date=2026-09-22&end_date=2026-09-22')
    assert response.status_code == 200
    assert response.content.startswith(b'\xef\xbb\xbf')
    assert len(response.content.decode('utf-8-sig').splitlines()) == 3
    assert ':T55+2:' in response.content.decode('utf-8-sig')
    assert response.headers['cache-control'] == 'no-store'
    db.expire_all()
    assert [row.execution_status for row in rows] == ['exported', 'in_progress', 'completed', 'not_started', 'not_started', 'not_started', 'not_started']
    assert rows[0].reported_by == 'planner' and rows[0].reported_at is not None
    assert plan.revision > before
    revision = plan.revision
    assert db.scalar(select(func.count()).select_from(ProductionPlanVersion)) == 1
    run = db.scalar(select(IntegrationRun))
    assert run.payload['item_ids'] == [row.id for row in rows[:3]]
    response = client.post('/integrations/csb/download?target_date=2026-09-22')
    assert response.status_code == 200
    db.expire_all()
    assert plan.revision == revision
    assert db.scalar(select(func.count()).select_from(ProductionPlanVersion)) == 1


def test_failed_download_does_not_change_status_or_history(csb_context):
    db, client, rows, plan = csb_context
    for query in ('target_date=2026-09-24', 'start_date=2026-09-23&end_date=2026-09-22'):
        assert client.post('/integrations/csb/download?' + query).status_code == 422
    db.expire_all()
    assert rows[0].execution_status == 'not_started'
    assert db.scalar(select(func.count()).select_from(IntegrationRun)) == 0
    assert db.scalar(select(func.count()).select_from(ProductionPlanVersion)) == 0


@pytest.mark.parametrize('value,expected', [('3.6', '4'), ('3.0', '3'), ('0.01', '1'), ('3,6', '4')])
def test_csb_exports_web_plan_kg_without_rounding(csb_context, value, expected):
    db, client, rows, plan = csb_context
    item = rows[0]
    item.source_unit = 'шт'
    item.source_quantity = Decimal(value.replace(',', '.'))
    item.line.csb_t55 = value
    content, ids = build_csb_text([item])
    assert 'PROD-ORDER:L1+10:' in content
    assert f':T55+{expected}:' in content
    assert item.source_quantity == Decimal(value.replace(',', '.'))
    assert item.required_hours == Decimal('1.5')
    item.source_unit = 'кг'
    item.quantity_kg = Decimal('0.9')  # 0.9 kg / 0.25 kg per piece = 3.6 pieces
    assert 'PROD-ORDER:L1+0.9:' in build_csb_text([item])[0]


@pytest.mark.parametrize('value', ['bad', 'NaN', 'Infinity', '-1'])
def test_invalid_duration_does_not_mark_tasks_exported(csb_context, value):
    db, client, rows, plan = csb_context
    rows[0].line.csb_t55 = value
    db.commit()
    response = client.post('/integrations/csb/download?target_date=2026-09-22')
    assert response.status_code == 422
    assert rows[0].execution_status == 'not_started'
    assert db.scalar(select(func.count()).select_from(IntegrationRun)) == 0
