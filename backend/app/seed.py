from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import joinedload
from app.core.config import settings
from app.core.line_catalog import LINE_BY_KEY, LINE_DEFINITIONS, line_key
from app.core.security import LOCAL_USER_MARKER, configured_local_users

from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models.entities import DemandItem, LineCapacity, PlanningRule, ProductionLine, ProductionPlan, User
from app.services.line_schedule_service import DEFAULT_ANCHOR, default_schedule_code, ensure_line_capacities
from app.services.plan_service import PlanService


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
            first_by_name: dict[str, ProductionLine] = {}
            for line in lines:
                key = line_key(line.name)
                definition = LINE_BY_KEY.get(key)
                if not definition or key in first_by_name:
                    line.status = "inactive"
                    continue
                first_by_name[key] = line
                line.workshop_code, line.workshop_name, canonical_name, line.csb_line_code = definition
                line.name = canonical_name
                line.status = "active"
                line.schedule_code = line.schedule_code or default_schedule_code(line.name)
                line.schedule_anchor_date = line.schedule_anchor_date or DEFAULT_ANCHOR
            priority = max((line.priority for line in lines), default=0) + 10
            for workshop_code, workshop_name, line_name, csb_code in LINE_DEFINITIONS:
                if line_key(line_name) in first_by_name:
                    continue
                line = ProductionLine(
                    code=f"FK-{workshop_code}-{priority:03d}", name=line_name,
                    workshop_code=workshop_code, workshop_name=workshop_name,
                    production_day_start_hour=15 if workshop_code == "PC" else 0,
                    working_hours=Decimal("22"), default_capacity=Decimal("0"),
                    capacity_unit="кг/день", priority=priority, status="active",
                    schedule_code=default_schedule_code(line_name), schedule_anchor_date=DEFAULT_ANCHOR,
                    csb_line_code=csb_code, csb_t5="4",
                    comments="Каноническая структура линий ФК",
                )
                db.add(line); lines.append(line); first_by_name[line_key(line_name)] = line
                priority += 10
            active_lines = [line for line in lines if line.status == "active"]
            active_plan = db.scalar(select(ProductionPlan).where(ProductionPlan.active.is_(True)).order_by(ProductionPlan.updated_at.desc()))
            if active_plan:
                ensure_line_capacities(db, active_lines, active_plan.horizon_start, active_plan.horizon_end, refresh_generated=True)
                capacity = {
                    (row.line_id, row.capacity_date, row.shift): row.available and Decimal(row.available_hours) > 0
                    for row in db.scalars(select(LineCapacity).where(
                        LineCapacity.capacity_date >= active_plan.horizon_start,
                        LineCapacity.capacity_date <= active_plan.horizon_end,
                    ))
                }
                production_items = [item for item in active_plan.schedule_items if item.schedule_kind == "production"]
                invalid_slots = any(
                    item.line_id and item.production_date and not capacity.get((item.line_id, item.production_date, item.shift), False)
                    for item in production_items
                )
                service = PlanService(db)
                if invalid_slots:
                    demand_ids = {item.demand_item_id for item in production_items if item.demand_item_id}
                    demands = list(db.scalars(
                        select(DemandItem).where(DemandItem.id.in_(demand_ids)).options(joinedload(DemandItem.product))
                    ))
                    if demands:
                        service.calculate(active_plan, demands, "line_schedules_applied")
                else:
                    service.refresh_sequence_and_cleanings(active_plan)
                    service.recalculate_load(active_plan)
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
