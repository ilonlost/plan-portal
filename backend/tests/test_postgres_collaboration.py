"""Optional integration checks against an isolated PostgreSQL, never a production DB."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema, DropSchema

from app.db.base import Base
from app.db.session import get_db
from app.api.routes import plans
from app.core.security import UserContext, create_session_token
from app.core.config import settings
from app.models.entities import ProductionLine, ProductionScheduleItem, User
from tests.test_audit_regressions import make_plan


def test_simultaneous_postgres_writes_have_one_winner(monkeypatch):
    url = os.environ.get("PLAN_AUDIT_POSTGRES_URL")
    if not url:
        pytest.skip("Set PLAN_AUDIT_POSTGRES_URL to an isolated test PostgreSQL")
    engine = create_engine(url)
    schema = "audit_" + uuid4().hex
    with engine.begin() as connection:
        connection.execute(CreateSchema(schema))
    isolated = engine.execution_options(schema_translate_map={None: schema})
    try:
        Base.metadata.create_all(isolated)
        with Session(isolated, expire_on_commit=False) as db:
            db.add(ProductionLine(code="test", name="Сэндвичи", workshop_code="KC", workshop_name="КЦ", working_hours=22, schedule_code="two_shift_daily"))
            db.add(User(username="regular.admin", display_name="Admin", role="admin"))
            db.commit()
            plan, _ = make_plan(db)
            plan_id, version = plan.id, plan.revision
            item_id = next(i.id for i in plan.schedule_items if i.schedule_kind == "production")
        monkeypatch.setattr(settings, "auth_mode", "ldap")
        monkeypatch.setattr(settings, "app_env", "production")
        monkeypatch.setattr(settings, "session_secret", "test-secret-with-more-than-32-characters")
        app = FastAPI(); app.include_router(plans.router)
        def get_test_db():
            with Session(isolated, expire_on_commit=False) as db:
                yield db
        app.dependency_overrides[get_db] = get_test_db
        token = create_session_token(UserContext("regular.admin", "Admin", "admin"))
        barrier = Barrier(2)
        def update(quantity):
            with TestClient(app) as client:
                client.cookies.set(settings.session_cookie_name, token)
                barrier.wait(timeout=10)
                return client.patch(f"/plans/{plan_id}/items/{item_id}", json={"quantity": quantity}, headers={"If-Match": str(version)})
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(update, [80, 90]))
        assert sorted(r.status_code for r in results) == [200, 409], [r.text for r in results]
        with Session(isolated) as db:
            assert db.get(ProductionScheduleItem, item_id).quantity in {80, 90}
    finally:
        with engine.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))
        engine.dispose()
