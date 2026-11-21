"""Calendar execution intervals within a line's production day (factory time)."""
from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal

from app.services.production_cycle_service import cycle_window, work_intervals


def shift_segments(start, end):
    segments = []
    cursor = start
    while cursor < end:
        night = cursor.hour >= 20 or cursor.hour < 8
        boundary = datetime.combine(cursor.date(), time(8 if cursor.hour < 8 else 20))
        if cursor.hour >= 20:
            boundary = datetime.combine(cursor.date() + timedelta(days=1), time(8))
        finish = min(end, boundary)
        segments.append({"shift": "night" if night else "day", "start_at": cursor, "end_at": finish})
        cursor = finish
    return segments


def bind_task_timeline(items):
    groups = defaultdict(list)
    for item in items:
        item._task_timeline = {}
        item._task_timeline_revision = item.plan.revision if item.plan else None
        if not item.excluded and item.production_date and item.line:
            groups[(item.line_id, item.production_date)].append(item)
    for rows in groups.values():
        intervals = work_intervals(rows[0].line, rows[0].production_date)
        if not intervals:
            continue
        opening, closing = intervals[0][0], intervals[-1][1]
        cursor = opening
        for row in sorted(rows, key=lambda r: (r.sequence, r.id or 0)):
            explicit = None
            if row.schedule_kind != "production" and row.start_time:
                explicit = datetime.combine(opening.date(), row.start_time)
                if explicit < opening:
                    explicit += timedelta(days=1)
            start = max(cursor, explicit) if explicit else cursor
            remaining = timedelta(seconds=int(Decimal(row.required_hours or 0) * 3600))
            segments = []
            end = start
            for left, right in intervals:
                if end >= right:
                    continue
                end = max(end, left)
                if not segments:
                    start = end
                used = min(remaining, right - end)
                segments.extend(shift_segments(end, end + used))
                end += used
                remaining -= used
                if not remaining:
                    break
            if remaining:
                segments.extend(shift_segments(end, end + remaining))
                end += remaining
            row._task_timeline = {"task_start_at": start, "task_end_at": end,
                "calendar_production_start": start.date(), "calendar_production_end": (end - timedelta(microseconds=1)).date() if end > start else start.date(),
                "shift_segments": segments, "outside_production_window": end > closing}
            cursor = end


def task_timeline_fields(item):
    if not item.line or not item.production_date or not cycle_window(item.line, item.production_date):
        return {}
    if not hasattr(item, "_task_timeline") or (item.plan and getattr(item, "_task_timeline_revision", None) != item.plan.revision):
        bind_task_timeline(item.plan.schedule_items if item.plan else [item])
    return item._task_timeline
