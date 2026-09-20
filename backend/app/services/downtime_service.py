from __future__ import annotations

from datetime import date, datetime, time, timedelta

from sqlalchemy import create_engine, text

from app.core.config import settings


DEFAULT_QUERY = """
SELECT line_code, start_at, end_at, downtime_type, reason
FROM line_downtimes
WHERE start_at < :end_at AND end_at >= :start_at
ORDER BY start_at
"""


def load_downtimes(start: date, end: date) -> tuple[str, list[dict]]:
    """Read external downtime rows through a read-only configurable query.

    The query must expose line_code, start_at, end_at, downtime_type and reason.
    Credentials and the actual ERP/table layout stay in environment variables.
    """
    if not settings.downtime_database_url.strip():
        return "not_configured", []
    query = settings.downtime_query.strip() or DEFAULT_QUERY
    start_at = datetime.combine(start, time.min)
    end_at = datetime.combine(end + timedelta(days=1), time.min)
    engine = None
    try:
        engine = create_engine(settings.downtime_database_url, pool_pre_ping=True, pool_recycle=300)
        with engine.connect() as connection:
            rows = connection.execute(text(query), {"start_at": start_at, "end_at": end_at}).mappings().all()
        result = []
        for index, row in enumerate(rows):
            start_value, end_value = row.get("start_at"), row.get("end_at")
            if not isinstance(start_value, datetime) or not isinstance(end_value, datetime):
                continue
            kind = str(row.get("downtime_type") or "partial").strip().lower()
            result.append({
                "id": str(row.get("id") or f"external-{index}"),
                "line_code": str(row.get("line_code") or "").strip(),
                "start_at": start_value,
                "end_at": end_value,
                "downtime_type": "full" if kind in {"full", "полный", "complete"} else "partial",
                "reason": str(row.get("reason") or "Простой из внешней системы").strip(),
            })
        return "connected", result
    except Exception:
        # The plan must remain available when the external source is unavailable.
        return "unavailable", []
    finally:
        if engine is not None:
            engine.dispose()
