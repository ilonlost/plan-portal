from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import re

from sqlalchemy import create_engine, text

from app.core.config import settings


DEFAULT_QUERY = """
SELECT line_code, start_at, end_at, downtime_type, reason
FROM line_downtimes
WHERE start_at < :end_at AND COALESCE(end_at, :end_at) > :start_at
ORDER BY start_at
"""

# Field direction explicitly specified by the owner of the EQUIPMENT source.
# Do not silently swap reversed dates or treat repair completion as restart.
EQUIPMENT_QUERY = """
SELECT RN AS id, SEQNAME AS line_name, SDEPNAME AS workshop_name,
       EQUIP_START_DATE AS start_at, STOP_DATE AS end_at,
       SSTOP_TYPE AS downtime_type,
       COALESCE(NULLIF(CONVERT(nvarchar(max), SSTOP_REASON), N''),
                NULLIF(CONVERT(nvarchar(max), STOP_NOTE), N''), N'Причина не указана') AS reason
FROM EQUIPMENT.dbo.EQUIPMENT_STOP
WHERE (EQUIP_START_DATE < :end_at AND (STOP_DATE > :start_at OR STOP_DATE IS NULL))
   OR (EQUIP_START_DATE >= :start_at AND EQUIP_START_DATE < :end_at)
   OR (EQUIP_START_DATE IS NULL AND STOP_DATE >= :start_at AND STOP_DATE < :end_at)
ORDER BY EQUIP_START_DATE, RN
"""


def normalized_line_name(value: str) -> str:
    value = re.sub(r"\([^)]*\)", " ", value.casefold().replace("ё", "е"))
    value = re.sub(r"\b(линия|цех|производства|производство)\b", " ", value)
    value = " ".join(re.sub(r"[^а-яa-z0-9 ]", " ", value).split())
    return {
        "лазаньи": "лазанья", "сэндвичей": "сэндвичи", "сендвичей": "сэндвичи",
        "бургеров": "бургеры", "жареных блюд": "жареные блюда", "жаренных блюд": "жареные блюда",
        "готовых блюд": "миквак", "готовых обедов": "миквак", "миксвак": "миквак", "micvac": "миквак",
        "супов": "супы", "салатов": "салаты", "напитков": "напитки", "морсов": "напитки",
        "слойки": "слойка", "rondo слойка": "слойка", "упаковки слойки": "слойка",
        "булки": "булка", "упаковки булок": "булка", "хлеб": "хлеба", "хлебов": "хлеба", "упаковки хлеба": "хлеба",
        "сухарей": "сухари", "начинок": "начинка", "приготовления начинок": "начинка", "розлива напитков": "напитки",
        "ручной зоны": "ручная зона", "ручная зона пц": "ручная зона", "ручная зона кц": "ручная зона",
    }.get(value, value)


def resolve_downtime_line(row: dict, lines: list):
    """Resolve a unique name/code; ambiguous workshop names never pick a random line."""
    code = str(row.get("line_code") or "").strip().casefold()
    if code:
        exact = [line for line in lines if str(line.code).casefold() == code]
        if len(exact) == 1:
            return exact[0]
    name = normalized_line_name(str(row.get("line_name") or ""))
    candidates = [line for line in lines if name and normalized_line_name(line.name) == name]
    if not candidates and code:
        candidates = [line for line in lines if str(line.csb_line_code or "").casefold() == code]
    workshop = str(row.get("workshop_name") or "").strip().casefold()
    workshop_code = {"кулинария": "KC", "кц": "KC", "kc": "KC", "пекарня": "PC", "пц": "PC", "pc": "PC"}.get(workshop)
    if workshop_code:
        candidates = [line for line in candidates if line.workshop_code == workshop_code]
    return candidates[0] if len(candidates) == 1 else None


def load_downtimes(start: date, end: date) -> tuple[str, list[dict]]:
    """Read external downtime rows through a read-only configurable query.

    The query must expose line_code, start_at, end_at, downtime_type and reason.
    Credentials and the actual ERP/table layout stay in environment variables.
    """
    if not settings.downtime_database_url.strip():
        return "not_configured", []
    is_mssql = settings.downtime_database_url.startswith("mssql+")
    query = settings.downtime_query.strip() or (EQUIPMENT_QUERY if is_mssql else DEFAULT_QUERY)
    start_at = datetime.combine(start, time.min)
    end_at = datetime.combine(end + timedelta(days=1), time.min)
    engine = None
    try:
        options = {"connect_args": {"login_timeout": 8, "timeout": 15}} if settings.downtime_database_url.startswith("mssql+pymssql:") else {}
        engine = create_engine(settings.downtime_database_url, pool_pre_ping=True, pool_recycle=300, **options)
        with engine.connect() as connection:
            rows = connection.execute(text(query), {"start_at": start_at, "end_at": end_at}).mappings().all()
        result = []
        for index, row in enumerate(rows):
            # These records explicitly say that the equipment was not stopped.
            if str(row.get("downtime_type") or "").strip().casefold() in {"без простоя", "none", "no downtime"}:
                continue
            identity = {"id": str(row.get("id") or f"external-{index}"),
                        "line_code": str(row.get("line_code") or "").strip(),
                        "line_name": str(row.get("line_name") or "").strip(),
                        "workshop_name": str(row.get("workshop_name") or "").strip()}
            start_value, end_value = row.get("start_at"), row.get("end_at")
            try:
                if isinstance(start_value, str):
                    start_value = datetime.fromisoformat(start_value)
                if isinstance(end_value, str):
                    end_value = datetime.fromisoformat(end_value)
            except ValueError:
                result.append({**identity, "data_error": "Некорректный формат даты простоя"})
                continue
            if end_value is None:
                end_value = datetime.now(timezone(timedelta(hours=3)))
            if not isinstance(start_value, datetime) or not isinstance(end_value, datetime):
                result.append({**identity, "data_error": "Не указано начало простоя"})
                continue
            # ERP naive timestamps are Moscow local time. Normalize aware values
            # before splitting at local calendar-day boundaries.
            local_zone = timezone(timedelta(hours=3))
            start_value = start_value.replace(tzinfo=local_zone) if start_value.tzinfo is None else start_value.astimezone(local_zone)
            end_value = end_value.replace(tzinfo=local_zone) if end_value.tzinfo is None else end_value.astimezone(local_zone)
            if end_value <= start_value:
                result.append({**identity, "data_error": "Конец простоя не позже начала", "start_at": start_value, "end_at": end_value})
                continue
            start_value = max(start_value, start_at.replace(tzinfo=local_zone))
            end_value = min(end_value, end_at.replace(tzinfo=local_zone))
            if end_value <= start_value:
                continue
            kind = str(row.get("downtime_type") or "").strip().lower()
            kind = "full" if kind in {"full", "полный", "полная", "полный простой", "полная остановка", "простой линии", "complete"} else "partial" if kind in {"partial", "частичный", "частичная", "частичный простой", "частичная остановка"} else "unknown"
            while start_value < end_value:
                part_end = min(end_value, datetime.combine(start_value.date() + timedelta(days=1), time.min, local_zone))
                result.append({
                    **identity,
                    "id": f"{identity['id']}-{start_value.date()}",
                    "start_at": start_value,
                    "end_at": part_end,
                    "downtime_type": kind,
                    "reason": str(row.get("reason") or "Простой из внешней системы").strip(),
                })
                start_value = part_end
        return "connected", result
    except Exception:
        # The plan must remain available when the external source is unavailable.
        return "unavailable", []
    finally:
        if engine is not None:
            engine.dispose()
