from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from app.core.config import settings
from app.core.line_catalog import LINE_DEFINITIONS
from app.core.security import LOCAL_USER_MARKER, configured_local_users

from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models.entities import PlanningRule, ProductionLine, ProductionPlan, User
from app.services.line_schedule_service import DEFAULT_ANCHOR, default_schedule_code


WORKSHOPS = {
    "PC": ("ПЦ", [name for code, _, name, _ in LINE_DEFINITIONS if code == "PC"]),
    "KC": ("КЦ", [name for code, _, name, _ in LINE_DEFINITIONS if code == "KC"]),
}


def seed() -> None:
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        existing_users = {user.username: user for user in db.scalars(select(User))}
        if settings.local_auth_enabled:
            for context, _ in configured_local_users().values():
                user = existing_users.get(context.username)
                if not user:
                    user = User(username=context.username)
                    db.add(user)
                user.display_name = context.display_name
                user.role = context.role
                user.email = context.email or None
                user.workshop_code = context.workshop_code
                user.line_name = context.line_name
                user.ldap_groups = [LOCAL_USER_MARKER]
                user.active = True
        else:
            for user in existing_users.values():
                if user.username.lower() in {"demo.admin", "demo.planner"} or LOCAL_USER_MARKER in (user.ldap_groups or []):
                    user.active = False
        lines = list(db.scalars(select(ProductionLine).order_by(ProductionLine.id)))
        if lines:
            # Initialization must not rewrite an existing catalog or regenerate
            # tasks on every container restart. Changes belong to migrations or
            # explicit planner actions, with revision control and audit.
            db.commit()
            return
        priority = 10
        for workshop_code, workshop_name, line_name, csb_code in LINE_DEFINITIONS:
            db.add(ProductionLine(
                code=f"FK-{workshop_code}-{priority:02d}", name=line_name,
                workshop_code=workshop_code, workshop_name=workshop_name,
                production_day_start_hour=15 if workshop_code == "PC" else 0,
                working_hours=Decimal("22"), default_capacity=Decimal("0"),
                capacity_unit="кг/день", priority=priority,
                schedule_code=default_schedule_code(line_name), schedule_anchor_date=DEFAULT_ANCHOR,
                csb_line_code=csb_code, csb_t5="4",
                comments="Структура из файла «План производства 12.08.2026» · 22 ч производства + 2 ч обеда",
            ))
            priority += 10
        db.add_all([
            PlanningRule(code="OHL_FIRST", name="ОХЛ занимает мощности первым", priority=10, parameters={"exact_date": True}),
            PlanningRule(code="ADVANCE_MARKING", name="Сэндвичи и бургеры: ДП = ДМ − 1", priority=20, parameters={"days": 1}),
            PlanningRule(code="ZAM_RESIDUAL", name="ЗАМ заполняет остаточную мощность", priority=30, parameters={"target_load_percent": 100}),
            PlanningRule(code="FINITE_CAPACITY", name="Не скрывать превышение мощности", priority=40, parameters={"conflict_on_overload": True}),
        ])
        today = date.today()
        db.add(ProductionPlan(
            name="Ожидает загрузки Excel", horizon_start=today,
            horizon_end=today + timedelta(days=20), active=True,
        ))
        db.commit()


if __name__ == "__main__":
    seed()
