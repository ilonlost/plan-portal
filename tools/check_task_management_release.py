"""Release QA on an isolated restored database. Never targets the live database."""
import os
import json
import secrets
import importlib.util
from pathlib import Path
from datetime import date, timedelta
from decimal import Decimal
from http.cookies import SimpleCookie

from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from app.core.config import settings

name = os.environ.get("RELEASE_CHECK_DATABASE", "")
url = make_url(settings.database_url)
if name != "plan_release_0212_qa" or name == url.database:
    raise RuntimeError("This check requires the isolated plan_release_0212_qa database")
settings.database_url = url.set(database=name).render_as_string(hide_password=False)

from app.db.session import engine
from sqlalchemy.orm import Session
from alembic.migration import MigrationContext
from alembic.operations import Operations
from starlette.requests import Request
from starlette.responses import Response
from fastapi import HTTPException
from app.core.security import UserContext, current_user
from app.models.entities import User, ProductionLine, ProductionScheduleItem
from app.schemas.common import ImportPreview, ImportRow, ImportConfirmRequest
from app.services.plan_service import PlanService
from app.services.production_cycle_service import production_cycle
from app.api.routes import imports, planner_admin, session

actor = UserContext("release-check", "Release check", "planner")
imports.send_notification = lambda *args, **kwargs: None


def fingerprint(conn):
    tables = ["production_schedule_items", "production_plans", "production_plan_versions", "demand_items", "products", "line_capabilities", "production_lines", "line_capacities", "imported_orders", "users", "audit_events", "auth_audit_events", "portal_settings"]
    return {t: tuple(conn.execute(text(f"SELECT count(*), md5(COALESCE(string_agg(row_to_json(t)::text, '' ORDER BY t.id), '')) FROM {t} t")).one()) for t in tables}


with engine.begin() as conn:
    path = Path("/app/alembic/versions/20261019_0022_scoped_imports_service_accounts.py")
    spec = importlib.util.spec_from_file_location("release_migration", path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(conn)):
        module.upgrade()

with engine.connect() as conn:
    before = fingerprint(conn); conn.rollback()
    outer = conn.begin()
    db = Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    try:
        plan = PlanService(db).active_plan()
        lines = list(db.scalars(select(ProductionLine).where(ProductionLine.status == "active")))
        target = next(r for r in lines if r.name == "Булка")
        candidates = [r for r in plan.schedule_items if r.line_id == target.id and r.schedule_kind == "production" and r.source_kind == "ohl" and r.source == "auto" and not r.excluded and r.production_date]
        selected = None
        for row in candidates:
            source = row.marking_date or row.demand_item.source_plan_date or row.production_date
            expected = source if production_cycle(target)["enabled"] else source - timedelta(days=int(row.product.advance_status == "АЗ"))
            if expected == row.production_date:
                selected = row; break
        assert selected, "No suitable OHL row on Bulka"
        day = selected.production_date
        preview = planner_admin.line_tasks(day, day, target.id, db, actor)
        assert preview["count"] == len(preview["items"]) > 0
        protected = {r.id: {c.name: str(getattr(r,c.name)) for c in ProductionScheduleItem.__table__.columns} for r in plan.schedule_items
                     if r.schedule_kind == "production" and not (r.line_id == target.id and r.source_kind == "ohl" and r.production_date == day)}
        dm = selected.marking_date or selected.demand_item.source_plan_date or day
        quantity = Decimal(selected.quantity_kg or selected.quantity)
        row = ImportRow(row_number=2, sku=selected.product.sku, product_name=selected.product.name, quantity=quantity,
            quantity_kg=quantity, source_quantity=quantity, source_unit="кг", requested_date=day, due_date=day,
            source_plan_date=dm, marking_date=dm, exact_date=True, valid=True)
        data = ImportPreview(file_name="release-check.xlsx", mapping_code="default_v1", template_type="ohl_daily", total_rows=1, valid_rows=1, invalid_rows=0, columns=[], rows=[row])
        result = imports.confirm_import(ImportConfirmRequest(preview=data,line_id=target.id,plan_version=plan.revision,start_date=day,end_date=day), db, actor)
        assert protected == {r.id: {c.name:str(getattr(r,c.name)) for c in ProductionScheduleItem.__table__.columns} for r in plan.schedule_items if r.id in protected}
        imported_id = result["order_id"]
        fresh = [r for r in plan.schedule_items if r.demand_item and r.demand_item.order_id == imported_id and not r.excluded]
        assert fresh and all(r.line_id == target.id and r.production_date == day for r in fresh)
        PlanService(db).recalculate_after_catalog_change()
        fresh = [r for r in plan.schedule_items if r.demand_item and r.demand_item.order_id == imported_id and not r.excluded]
        assert fresh and all(r.line_id == target.id and r.production_date == day for r in fresh)

        secret = secrets.token_urlsafe(24)
        created = planner_admin.create_service(planner_admin.ServiceAccountCreate(username="svc.qa." + secrets.token_hex(4),display_name="Release QA",password=secret),db,actor)
        account = db.get(User,created["id"])
        assert account.role == "viewer" and account.auth_provider == "service" and secret not in account.password_hash
        request = Request({"type":"http","method":"POST","path":"/api/session/service-login","headers":[],"client":("127.0.0.1",1)})
        response = Response()
        logged = session.service_login(session.LoginRequest(username=account.username,password=secret),request,response,db)
        assert logged["user"]["role"] == "viewer"
        cookie = SimpleCookie(); cookie.load(response.headers["set-cookie"])
        token = cookie[settings.session_cookie_name].value
        def service_request(method):
            return Request({"type":"http","method":method,"path":"/api/plans/active","headers":[(b"cookie",f"{settings.session_cookie_name}={token}".encode())]})
        assert current_user(service_request("GET"),db).role == "viewer"
        try:
            current_user(service_request("POST"),db)
            raise AssertionError("Service identity could write")
        except HTTPException as exc:
            assert exc.status_code == 403
        planner_admin.reset_service_password(account.id,planner_admin.ServicePasswordUpdate(password=secret+"new"),db,actor)
        try:
            current_user(service_request("GET"),db)
            raise AssertionError("Old service session survived reset")
        except HTTPException as exc:
            assert exc.status_code == 401
        print(json.dumps({"line":target.name,"date":str(day),"deletion_preview_tasks":preview["count"],"protected_production_cards":len(protected),"period_upload_verified":True,"rebuild_boundary_verified":True,"service_viewer_and_revocation_verified":True},ensure_ascii=False))
    finally:
        db.close(); outer.rollback()
    assert fingerprint(conn) == before
    print("ISOLATED_QA_ROLLBACK_VERIFIED")
