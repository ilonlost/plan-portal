"""Calendar windows for a line's production day (local factory time)."""
from datetime import date, datetime, time, timedelta
from decimal import Decimal
import re


DEFAULT_CYCLE = {"enabled": False, "start_time": "00:00", "duration_hours": 24.0}


def clock_minutes(value) -> int:
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise ValueError("Укажите время в формате ЧЧ:ММ")
    hour, minute = map(int, value.split(":"))
    return hour * 60 + minute


def validate_cycle(values: dict) -> dict:
    if type(values.get("enabled")) is not bool:
        raise ValueError("Укажите, включены ли производственные сутки")
    start = values.get("start_time", "")
    start_minutes = clock_minutes(start)
    result = {"enabled": values["enabled"], "start_time": start}
    if "end_time" in values:
        minutes = (clock_minutes(values["end_time"]) - start_minutes) % 1440 or 1440
        result["end_time"] = values["end_time"]
    else:
        minutes = Decimal(str(values.get("duration_hours", 24))) * 60
    if not Decimal(minutes).is_finite() or not 15 <= minutes <= 1440 or minutes != int(minutes):
        raise ValueError("Длительность суток: от 15 минут до 24 часов, с точностью до минуты")
    return {**result, "duration_hours": float(minutes / 60)}


def production_cycle(line) -> dict:
    return {**DEFAULT_CYCLE, **((line.planning_settings or {}).get("production_cycle") or {})}


def cycle_window(line, day: date) -> tuple[datetime, datetime] | None:
    """The plan column is the calendar date on which this cycle ends.

    E.g. a 24h cycle at 18:00 for day 19 Oct runs 18 Oct 18:00–19 Oct 18:00.
    Date labels (including ZAM DP=DM) remain separate from actual calendar bounds.
    """
    cycle = production_cycle(line)
    if not cycle["enabled"]:
        return None
    start = time.fromisoformat(cycle["start_time"])
    minutes = round(cycle["duration_hours"] * 60)
    end_minutes = (start.hour * 60 + start.minute + minutes) % 1440
    end = datetime.combine(day, time(end_minutes // 60, end_minutes % 60))
    return end - timedelta(minutes=minutes), end


def window_fields(line, day: date | None) -> dict:
    bounds = cycle_window(line, day) if line and day else None
    return {"production_window_start": bounds[0] if bounds else None,
            "production_window_end": bounds[1] if bounds else None}


def cap_shift_hours(line, day_hours: Decimal, night_hours: Decimal) -> tuple[Decimal, Decimal]:
    cycle = production_cycle(line)
    if not cycle["enabled"]:
        return day_hours, night_hours
    hours = Decimal(str(cycle["duration_hours"]))
    # Two shifts share a single calendar window. A day-only template may use it all.
    limit = hours / 2 if day_hours > 0 and night_hours > 0 else hours
    return min(day_hours, limit), min(night_hours, limit)
