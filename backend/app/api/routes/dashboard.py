from collections import defaultdict
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.core.security import UserContext, current_user
from app.models.entities import ProductionLine, ProductionScheduleItem, ScheduleStatus
from app.services.plan_service import PlanService

router = APIRouter(tags=["dashboard"])


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "production-planning-backend"}


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), user: UserContext = Depends(current_user)) -> dict:
    plan = PlanService(db).active_plan()
    if not plan:
        return {"active_plan": None, "metrics": {}, "line_loads": [], "problem_dates": []}
    items = list(db.scalars(select(ProductionScheduleItem).where(ProductionScheduleItem.plan_id == plan.id)))
    loads: dict[int, Decimal] = defaultdict(lambda: Decimal("0"))
    line_days: dict[int, int] = defaultdict(int)
    problems: dict[date, int] = defaultdict(int)
    for item in items:
        if item.line_id:
            loads[item.line_id] += Decimal(item.load_percent)
            line_days[item.line_id] += 1
        if item.production_date and item.status in {ScheduleStatus.CONFLICT, ScheduleStatus.UNSCHEDULED}:
            problems[item.production_date] += 1
    lines = list(db.scalars(select(ProductionLine).order_by(ProductionLine.priority)))
    avg_load = sum((Decimal(item.load_percent) for item in items if item.line_id), Decimal("0")) / max(1, len([item for item in items if item.line_id]))
    return {
        "active_plan": {"id": plan.id, "name": plan.name, "status": plan.status.value, "updated_at": plan.updated_at},
        "metrics": {
            "positions": len(items),
            "auto_planned": sum(item.source == "auto" and item.status != ScheduleStatus.UNSCHEDULED for item in items),
            "unscheduled": sum(item.status == ScheduleStatus.UNSCHEDULED for item in items),
            "conflicts": sum(item.status == ScheduleStatus.CONFLICT for item in items),
            "overloaded_lines": len({item.line_id for item in items if item.status == ScheduleStatus.CONFLICT and item.line_id}),
            "capacity_load": round(float(avg_load), 1),
        },
        "line_loads": [{
            "id": line.id, "code": line.code, "name": line.name,
            "load": round(float(loads[line.id] / max(1, line_days[line.id])), 1),
            "status": "danger" if loads[line.id] / max(1, line_days[line.id]) > 100 else "warning" if loads[line.id] / max(1, line_days[line.id]) >= 85 else "ok",
        } for line in lines],
        "problem_dates": [{"date": key, "count": value} for key, value in sorted(problems.items())[:5]],
    }
