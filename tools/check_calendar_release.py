"""Verify calendar presentation and edits on an isolated production database copy."""
import os
import json
import importlib.util
from pathlib import Path
from datetime import date, time
from collections import defaultdict
from decimal import Decimal
from time import perf_counter

from sqlalchemy.engine import make_url
from app.core.config import settings

url = make_url(settings.database_url)
if os.environ.get("RELEASE_CHECK_DATABASE") != "plan_release_0214_qa" or url.database == "plan_release_0214_qa":
    raise RuntimeError("Requires isolated plan_release_0214_qa database")
settings.database_url = url.set(database="plan_release_0214_qa").render_as_string(hide_password=False)

from sqlalchemy import text, select
from sqlalchemy.orm import Session
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.db.session import engine
from app.models.entities import ProductionScheduleItem, ProductionLine, LineCapability
from app.services.plan_service import PlanService, schedule_item_dict
from app.services.task_timeline_service import bind_task_timeline
from app.api.routes import lines, plans
from app.core.security import UserContext


def fingerprint(conn):
    tables = ["production_schedule_items", "production_plans", "production_plan_versions", "demand_items", "products", "line_capabilities", "production_lines", "line_capacities", "imported_orders", "portal_settings", "line_comment_messages", "audit_events"]
    return {t: tuple(conn.execute(text(f"SELECT count(*), md5(COALESCE(string_agg(row_to_json(t)::text, '' ORDER BY t.id), '')) FROM {t} t")).one()) for t in tables}


with engine.begin() as conn:
    path = Path('/app/alembic/versions/20261023_0023_shipping_overrides.py')
    spec = importlib.util.spec_from_file_location('release_migration',path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(conn)):
        module.upgrade()

with engine.connect() as conn:
    before = fingerprint(conn); conn.rollback()
    outer = conn.begin()
    db = Session(bind=conn,join_transaction_mode='create_savepoint',expire_on_commit=False)
    try:
        service = PlanService(db); plan = service.active_plan()
        # The live plan may have been cleared/replaced by planners. Add the
        # requested three-card example only inside this isolated transaction.
        sandwich = db.scalar(select(ProductionLine).where(ProductionLine.name=='Сэндвичи'))
        cap = db.scalar(select(LineCapability).where(LineCapability.line_id==sandwich.id,LineCapability.units_per_hour>0))
        assert cap
        for sequence,hours in [(1001,2),(1002,12),(1003,10)]:
            plan.schedule_items.append(ProductionScheduleItem(line=sandwich,product_id=cap.product_id,
                production_date=date(2026,10,22),marking_date=date(2026,10,23),sequence=sequence,
                quantity=100,quantity_kg=100,required_hours=hours,source='manual',schedule_kind='production',source_kind='ohl'))
        db.flush()
        start = perf_counter()
        matrix = plans.active_plan_matrix(start=date(2026,10,19),days=7,workshop_code=None,line_id=None,db=db,user=UserContext('qa','QA','planner'))
        elapsed = perf_counter()-start
        calendar_rows = [r for w in matrix['workshops'] for l in w['lines'] for c in l['cells'] for r in c['items'] if r.get('task_start_at')]
        assert calendar_rows and all(r['task_end_at'] >= r['task_start_at'] for r in calendar_rows)
        lasagna = db.scalar(select(ProductionLine).where(ProductionLine.name=='Лазанья'))
        lasagna_cap = db.scalar(select(LineCapability).where(LineCapability.line_id==lasagna.id,LineCapability.units_per_hour>0))
        assert lasagna_cap
        for sequence,kind,shift in [(2001,'ohl','day'),(2002,'zam','night'),(2003,'pf','day')]:
            plan.schedule_items.append(ProductionScheduleItem(line=lasagna,product_id=lasagna_cap.product_id,
                production_date=date(2026,10,22),marking_date=date(2026,10,23),sequence=sequence,
                quantity=100,quantity_kg=100,required_hours=1,source='manual',schedule_kind='production',source_kind=kind,shift=shift))
        db.flush()
        groups=defaultdict(list)
        for row in plan.schedule_items:
            if row.line and row.line.name == 'Лазанья' and not row.excluded and row.schedule_kind == 'production' and row.production_date:
                groups[row.production_date].append(row)
        day,rows = next((day,rows) for day,rows in groups.items() if len(rows)>1)
        rows.sort(key=lambda r:r.sequence); first,last=rows[0],rows[-1]
        protected = {r.id:(r.line_id,r.production_date,r.quantity) for r in plan.schedule_items if r.line_id!=last.line_id and r.schedule_kind=='production'}
        service.update_item(last,{'before_item_id':first.id,'production_date':day})
        assert last.sequence < first.sequence
        assert protected == {r.id:(r.line_id,r.production_date,r.quantity) for r in plan.schedule_items if r.id in protected}
        service.update_item(last,{'shipping_date':date(2026,10,26),'shipping_time':time(19,30)})
        service.recalculate_after_catalog_change()
        assert last.sequence < first.sequence
        data = schedule_item_dict(last)
        assert data['shipping_date']==date(2026,10,26) and data['shipping_time']==time(19,30)
        lines.send_notification=lambda *a,**k:None
        viewer=UserContext('qa.viewer','QA viewer','viewer',auth_method='ldap')
        a=lines.save_line_comment(last.line_id,day,lines.LineCommentUpdate(text='QA first message'),db,viewer)
        b=lines.save_line_comment(last.line_id,day,lines.LineCommentUpdate(text='QA reply'),db,viewer)
        assert a['comment']['id']!=b['comment']['id']
        print(json.dumps({'calendar_cards':len(calendar_rows),'week_matrix_seconds':round(elapsed,3),'lasagna_date':str(day),'reorder_survives_recalculation':True,'shipping_override_survives':True,'corporate_viewer_conversation':True,'protected_other_line_production_cards':len(protected)},ensure_ascii=False))
    finally:
        db.close();outer.rollback()
    assert fingerprint(conn)==before
    print('ISOLATED_QA_ROLLBACK_VERIFIED')
