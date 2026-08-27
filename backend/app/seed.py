from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from app.db.base import Base
from app.db.session import SessionLocal, engine
from app.models.entities import PlanningRule, ProductionLine, ProductionPlan, User


WORKSHOPS = {
    "PC": ("ПЦ", ["Булка", "Слойка", "Хлеба", "Ручная зона ПЦ", "Сухари"]),
    "KC": ("КЦ", ["Сэндвичи", "Жареные блюда", "Лазанья", "Миквак", "Бургеры", "Супы", "Салаты", "Напитки", "Ручная зона КЦ"]),
}


def seed() -> None:
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        demo_users = [
            User(username="demo.admin", display_name="Локальный администратор", role="admin", email="admin@localhost"),
            User(username="demo.planner", display_name="Анна · планер", role="planner", email="planner@localhost"),
            User(username="master.sandwich", display_name="Иван · мастер сэндвичей", role="master", workshop_code="KC", line_name="Сэндвичи"),
            User(username="master.sloyka", display_name="Ольга · мастер слойки", role="master", workshop_code="PC", line_name="Слойка"),
            User(username="viewer", display_name="Просмотр", role="viewer"),
        ]
        existing_users = set(db.scalars(select(User.username)))
        db.add_all(user for user in demo_users if user.username not in existing_users)
        if db.scalar(select(ProductionLine.id).limit(1)):
            db.commit()
            return
        priority = 10
        for workshop_code, (workshop_name, line_names) in WORKSHOPS.items():
            for line_name in line_names:
                db.add(ProductionLine(
                    code=f"FK-{workshop_code}-{priority:02d}", name=line_name,
                    workshop_code=workshop_code, workshop_name=workshop_name,
                    working_hours=Decimal("24"), default_capacity=Decimal("0"),
                    capacity_unit="кг/день", priority=priority,
                    comments="Структура из файла «План производства 12.08.2026»",
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
