from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select

from test_audit_regressions import db, client
from test_line_import_scope import two_lines
from app.models.entities import AuditEvent, ProductionScheduleItem
from app.services.plan_service import PlanService


@pytest.mark.parametrize("include_manual", [None, False, True])
@pytest.mark.parametrize("one_line", [False, True])
def test_preview_and_delete_obey_manual_option(client, db, monkeypatch, include_manual, one_line):
    plan, demand, _, target, other = two_lines(db, monkeypatch)
    day = date(2026,9,8)
    edited = next(r for r in plan.schedule_items if r.line_id == target and r.source_kind == 'zam')
    edited.source = 'manual'; edited.locked = True
    manual = ProductionScheduleItem(plan_id=plan.id, line_id=target, product_id=demand.product_id,
        production_date=day, sequence=99, quantity=10, quantity_kg=10, required_hours=Decimal('0.1'),
        schedule_kind='production',source_kind='pf',source='manual',locked=True)
    tech = ProductionScheduleItem(plan_id=plan.id,line_id=target,production_date=day,
        sequence=100,quantity=0,required_hours=1,schedule_kind='cleaning',source='manual',locked=True)
    later = ProductionScheduleItem(plan_id=plan.id,line_id=target,product_id=demand.product_id,
        production_date=date(2026,9,9),sequence=1,quantity=10,quantity_kg=10,required_hours=Decimal('0.1'),
        schedule_kind='production',source_kind='pf',source='manual',locked=True)
    plan.schedule_items.extend([manual,tech,later]); db.commit()
    before = list(plan.schedule_items)
    expected = {r.id for r in before if not r.excluded and r.schedule_kind=='production' and r.production_date==day
                and (not one_line or r.line_id==target) and (include_manual is True or r.source!='manual')}
    protected = {r.id for r in before if not r.excluded and r.schedule_kind=='production' and r.id not in expected}
    params = {'start':str(day),'end':str(day),**({'line_id':target} if one_line else {}),
              **({'include_manual':str(include_manual).lower()} if include_manual is not None else {})}
    preview = client.get('/planner-admin/line-tasks',params=params)
    assert preview.status_code==200,preview.text
    data = preview.json()
    assert data['count']==len(expected) and {r['id'] for r in data['items']}==expected
    payload={'start_date':str(day),'end_date':str(day),**({'line_id':target} if one_line else {}),
             **({'include_manual':include_manual} if include_manual is not None else {})}
    response=client.post(f'/plans/{plan.id}/bulk-delete-items',json=payload,headers={'If-Match':str(data['version'])})
    assert response.status_code==200,response.text
    assert response.json()['deleted_count']==len(expected)
    assert {r.id for r in plan.schedule_items if r.id in expected and r.excluded}==expected
    assert all(not r.excluded for r in plan.schedule_items if r.id in protected)
    assert not tech.excluded and tech in plan.schedule_items
    assert manual.excluded == (include_manual is True)
    assert edited.excluded == (include_manual is True)
    event=db.scalar(select(AuditEvent).where(AuditEvent.action=='schedule_items_bulk_deleted').order_by(AuditEvent.id.desc()))
    assert event.details['include_manual']==(include_manual is True)
    PlanService(db).calculate(plan,PlanService(db)._latest_source_demands())
    assert all(r.excluded for r in plan.schedule_items if r.id in expected)
    assert not tech.excluded and not later.excluded
