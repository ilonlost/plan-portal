"""Identical scope for the bulk deletion preview and confirmation."""
from app.models.entities import ProductionScheduleItem


def deletion_scope(plan_id, start, end, line_id=None, include_manual=False):
    return [
        ProductionScheduleItem.plan_id == plan_id,
        ProductionScheduleItem.production_date.between(start, end),
        ProductionScheduleItem.schedule_kind == "production",
        ProductionScheduleItem.excluded.is_(False),
        *([ProductionScheduleItem.line_id == line_id] if line_id is not None else []),
        *([] if include_manual else [ProductionScheduleItem.source != "manual"]),
    ]
