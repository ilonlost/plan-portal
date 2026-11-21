from datetime import datetime, timedelta
from types import SimpleNamespace
import pytest
from sqlalchemy import select
from app.api.routes import admin, lines
from app.core.config import settings
from app.core.security import UserContext, create_session_token
from app.models.entities import User, ProductionLine, AuditEvent
from app.services.tail_buffer_service import MOSCOW
from test_audit_regressions import client, db


@pytest.fixture(autouse=True)
def no_mail(monkeypatch):
    monkeypatch.setattr(admin, 'send_notification', lambda *a, **kw: None)
    monkeypatch.setattr(lines, 'send_notification', lambda *a, **kw: SimpleNamespace(status='skipped'))


def sign_in(client, user):
    client.cookies.set(settings.session_cookie_name, create_session_token(UserContext(user.username, user.display_name, user.role)))


@pytest.mark.parametrize('role', ['admin', 'planner', 'viewer'])
def test_grant_and_revoke_apply_to_existing_session(client, db, role):
    actor=db.scalar(select(User).where(User.username=='regular.admin'))
    target=actor if role=='admin' else User(username='attendance.'+role, display_name='Test user', role=role)
    db.add(target);db.commit()
    line=db.scalar(select(ProductionLine).where(ProductionLine.name=='Булка'))
    today=datetime.now(MOSCOW).date()
    url=f'/lines/{line.id}/actual-attendance/{today}'
    sign_in(client,target)
    assert client.get('/session/me').json()['can_record_attendance'] is False
    assert client.put(url,json={'headcount':3}).status_code==403
    token=client.cookies.get(settings.session_cookie_name)
    sign_in(client,actor)
    assert client.patch(f'/admin/users/{target.id}',json={'role':role,'can_record_attendance':True}).status_code==200
    assert client.patch(f'/admin/users/{target.id}',json={'role':role,'section_permissions':{'plan':True}}).status_code==200
    client.cookies.set(settings.session_cookie_name,token)
    assert client.get('/session/me').json()['can_record_attendance'] is True
    response=client.put(url,json={'headcount':3})
    assert response.status_code==200,response.text
    assert response.json()['attendance']['author_name']==target.display_name
    assert client.put(f'/lines/{line.id}/actual-attendance/{today+timedelta(days=1)}',json={'headcount':3}).status_code==422
    sign_in(client,actor)
    assert client.patch(f'/admin/users/{target.id}',json={'role':role,'can_record_attendance':False}).status_code==200
    client.cookies.set(settings.session_cookie_name,token)
    assert client.get('/session/me').json()['can_record_attendance'] is False
    assert client.put(url,json={'headcount':4}).status_code==403
    assert any(e.details.get('can_record_attendance') is False for e in db.scalars(select(AuditEvent).where(AuditEvent.action=='user_access_updated')))


def test_master_default_scope_and_disable_survive_other_rights_edits(client,db):
    actor=db.scalar(select(User).where(User.username=='regular.admin'))
    line=db.scalar(select(ProductionLine).where(ProductionLine.name=='Булка'))
    other=db.scalar(select(ProductionLine).where(ProductionLine.name=='Хлеба'))
    target=User(username='master.test',display_name='Master',role='master',line_name=line.name,workshop_code=line.workshop_code)
    db.add(target);db.commit();day=datetime.now(MOSCOW).date()
    sign_in(client,target)
    assert client.get('/session/me').json()['can_record_attendance'] is True
    assert client.put(f'/lines/{other.id}/actual-attendance/{day}',json={'headcount':1}).status_code==403
    sign_in(client,actor)
    grant=f'/admin/users/{target.id}'
    assert client.patch(grant,json={'role':'master','can_record_attendance':False}).status_code==200
    assert client.patch(grant,json={'role':'master','section_permissions':{'plan':True,'can_record_attendance':True}}).status_code==200
    sign_in(client,target)
    assert client.get('/session/me').json()['can_record_attendance'] is False
    assert client.put(f'/lines/{line.id}/actual-attendance/{day}',json={'headcount':1}).status_code==403


def test_planner_cannot_grant_permission_or_smuggle_it_as_section(client,db):
    planner=User(username='planner.test',display_name='Planner',role='planner')
    target=User(username='viewer.test',display_name='Viewer',role='viewer')
    db.add_all([planner,target]);db.commit();sign_in(client,planner)
    assert client.patch(f'/admin/users/{target.id}',json={'role':'viewer','can_record_attendance':True}).status_code==403
    assert client.patch(f'/planner-admin/users/{target.id}',json={'role':'viewer','can_record_attendance':True}).status_code==422
    # The existing planner access endpoint delegates section edits to admin's implementation.
    result=client.patch(f'/planner-admin/users/{target.id}',json={'role':'viewer','section_permissions':{'plan':True,'can_record_attendance':True}})
    assert result.status_code==200,result.text
    sign_in(client,target)
    assert client.get('/session/me').json()['can_record_attendance'] is False


def test_service_accounts_and_hidden_plan_cannot_submit(client,db):
    actor=db.scalar(select(User).where(User.username=='regular.admin'))
    service=User(username='svc.test',display_name='Service',role='viewer',auth_provider='service')
    target=User(username='hidden.test',display_name='Hidden',role='viewer')
    db.add_all([service,target]);db.commit()
    assert client.patch(f'/admin/users/{service.id}',json={'role':'viewer','can_record_attendance':True}).status_code==422
    assert client.patch(f'/admin/users/{target.id}',json={'role':'viewer','can_record_attendance':True,'section_permissions':{'plan':False}}).status_code==200
    sign_in(client,target)
    line=db.scalar(select(ProductionLine))
    assert client.put(f'/lines/{line.id}/actual-attendance/{datetime.now(MOSCOW).date()}',json={'headcount':1}).status_code==403
