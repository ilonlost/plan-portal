from datetime import date
from decimal import Decimal
from sqlalchemy import select

from test_audit_regressions import db, client, make_plan
from test_line_import_scope import two_lines, snapshot, preview
from app.models.entities import DemandItem, ProductionScheduleItem, User, AuditEvent, LineCapacity
from app.services.plan_service import PlanService
from app.services.settings_service import save_planner_configuration
from app.core.security import UserContext, create_session_token
from app.core.config import settings
from app.api.routes import plans, admin, imports


def period_plan(db, monkeypatch):
    plan, first, second, target, other = two_lines(db, monkeypatch)
    extra = DemandItem(order_id=first.order_id, product_id=first.product_id, sku=first.sku, product_name=first.product_name,
        source_row=5, quantity=50, source_quantity=50, source_unit="кг", source_kind="ohl", exact_date=True,
        source_plan_date=date(2026,9,9), marking_date=date(2026,9,9), requested_date=date(2026,9,9), due_date=date(2026,9,9))
    db.add(extra); db.flush(); PlanService(db).calculate(plan, PlanService(db)._latest_source_demands())
    return plan, first, target, other


def test_delete_preview_lists_exact_items_and_all_lines(client, db, monkeypatch):
    plan, first, target, other = period_plan(db, monkeypatch)
    start = "2026-09-08"
    for line in (None, target):
        got = client.get("/planner-admin/line-tasks", params={"start":start,"end":start, **({"line_id":line} if line else {})})
        assert got.status_code == 200
        body = got.json()
        assert body["count"] == len(body["items"]) > 0
        assert all(row["production_date"] == start for row in body["items"])
        if line:
            ids={row["id"] for row in body["items"]}
            protected = snapshot(plan, other)
            deleted=client.post(f"/plans/{plan.id}/bulk-delete-items", json={"line_id":line,"start_date":start,"end_date":start}, headers={"If-Match":str(body["version"])})
            assert deleted.status_code == 200, deleted.text
            assert {row.id for row in plan.schedule_items if row.excluded and row.id in ids} == ids
            assert snapshot(plan, other) == protected


def test_period_ohl_upload_keeps_other_days_lines_and_zam(client, db, monkeypatch):
    plan, first, target, other = period_plan(db, monkeypatch)
    protected = {row.id: {col.name:getattr(row,col.name) for col in ProductionScheduleItem.__table__.columns}
        for row in plan.schedule_items if row.line_id != target or row.source_kind == "zam" or row.production_date == date(2026,9,9)}
    p=preview(35, skus=("101",))
    response=client.post("/imports/confirm", json={"preview":p.model_dump(mode="json"),"line_id":target,"plan_version":plan.revision,"start_date":"2026-09-08","end_date":"2026-09-08"})
    assert response.status_code == 200, response.text
    assert protected == {row.id:{col.name:getattr(row,col.name) for col in ProductionScheduleItem.__table__.columns} for row in plan.schedule_items if row.id in protected}
    def volume(day): return sum(row.quantity for row in plan.schedule_items if not row.excluded and row.line_id==target and row.source_kind=="ohl" and row.production_date==day)
    assert volume(date(2026,9,8)) == 35 and volume(date(2026,9,9)) == 50
    PlanService(db).recalculate_after_catalog_change()
    assert volume(date(2026,9,8)) == 35 and volume(date(2026,9,9)) == 50
    source = client.get('/planner-admin').json()['sources'][0]
    assert source['scope_start'] == source['scope_end'] == '2026-09-08'
    assert client.post("/imports/confirm",json={"preview":p.model_dump(mode="json"),"line_id":target,"plan_version":plan.revision,"start_date":"2026-09-10","end_date":"2026-09-11"}).status_code == 422


def test_weekly_zam_partial_replacement_survives_rebuild_and_source_deletion(client, db, monkeypatch):
    plan, first, target, other = period_plan(db, monkeypatch)
    save_planner_configuration(db, {"placement":{"complete_zam":False}}, "test")
    zam = db.scalar(select(DemandItem).where(DemandItem.source_kind == 'zam'))
    zam.quantity=zam.source_quantity=Decimal(200); zam.due_date=date(2026,9,9)
    slot=db.scalar(select(LineCapacity).where(LineCapacity.line_id==target,LineCapacity.capacity_date==date(2026,9,9),LineCapacity.shift=='day'))
    slot.available_hours=2; slot.manual_override=True
    db.flush(); PlanService(db).calculate(plan,PlanService(db)._latest_source_demands())
    outside={row.id: {col.name:getattr(row,col.name) for col in ProductionScheduleItem.__table__.columns} for row in plan.schedule_items if row.line_id==target and row.production_date==date(2026,9,9)}
    p=preview(40,kind='quarter_weekly',skus=('101',));p.rows[0].due_date=date(2026,9,9)
    response=client.post('/imports/confirm',json={'preview':p.model_dump(mode='json'),'line_id':target,'plan_version':plan.revision,'start_date':'2026-09-08','end_date':'2026-09-08'})
    assert response.status_code==200,response.text
    order_id=response.json()['order_id']
    assert outside=={row.id:{col.name:getattr(row,col.name) for col in ProductionScheduleItem.__table__.columns} for row in plan.schedule_items if row.id in outside}
    def volumes():
        return {day:sum(row.quantity for row in plan.schedule_items if row.line_id==target and row.production_date==day and row.source_kind=='zam' and not row.excluded) for day in (date(2026,9,8),date(2026,9,9))}
    assert volumes()=={date(2026,9,8):40,date(2026,9,9):100}
    PlanService(db).recalculate_after_catalog_change()
    assert volumes()=={date(2026,9,8):40,date(2026,9,9):100}
    assert client.delete(f'/planner-admin/sources/{order_id}').status_code==200
    PlanService(db).recalculate_after_catalog_change()
    assert volumes()=={date(2026,9,8):0,date(2026,9,9):100}


def test_service_login_alongside_sso_readonly_and_revocation(client, db, monkeypatch):
    monkeypatch.setattr(admin,'send_notification',lambda *a,**k:None)
    plan,_=make_plan(db)
    secret='test-only-password-Long12'
    result=client.post('/planner-admin/service-accounts',json={'username':'svc.screen','display_name':'Screen','password':secret})
    assert result.status_code==200,result.text
    user_id=result.json()['id']; account=db.get(User,user_id)
    assert account.role=='viewer' and secret not in account.password_hash
    assert secret not in str([event.details for event in db.scalars(select(AuditEvent))])
    monkeypatch.setattr(settings,'art_portal_sso_required',True)
    assert client.post('/session/login',json={'username':'svc.screen','password':secret}).status_code==403
    client.cookies.clear()
    assert client.post('/session/service-login',json={'username':'svc.screen','password':secret}).status_code==200
    old_cookie=client.cookies.get(settings.session_cookie_name)
    assert client.get('/plans/active').status_code==200
    assert client.post('/plans/active/recalculate').status_code==403
    assert client.get('/planner-admin').status_code==403
    assert client.get('/admin/overview').status_code==403
    client.cookies.set(settings.session_cookie_name,create_session_token(UserContext('regular.admin','Administrator','admin')))
    assert client.patch(f'/planner-admin/users/{user_id}',json={'role':'planner','active':True}).status_code==422
    assert client.put(f'/planner-admin/service-accounts/{user_id}/password',json={'password':secret+'new'}).status_code==200
    client.cookies.set(settings.session_cookie_name,old_cookie)
    assert client.get('/session/me').status_code==401
    client.cookies.clear()
    assert client.post('/session/service-login',json={'username':'svc.screen','password':secret+'new'}).status_code==200
    assert client.post('/planner-admin/service-accounts',json={'username':'svc.bad','display_name':'X','password':secret}).status_code==403
    assert client.get('/session/mode').json()=={'auth_mode':'art_portal_sso'}


def test_service_attempts_limited_and_ad_identity_not_overwritten(client, db):
    data={'username':'svc.test','display_name':'Screen','password':'test-password-12345'}
    assert client.post('/planner-admin/service-accounts',json=data).status_code==200
    assert client.post('/planner-admin/service-accounts',json=data).status_code==409
    client.cookies.clear()
    for _ in range(8): assert client.post('/session/service-login',json={'username':data['username'],'password':'wrong'}).status_code==401
    assert client.post('/session/service-login',json={'username':data['username'],'password':data['password']}).status_code==429
