"""Today's unique sanitary checkpoint arrivals by assigned production line."""
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from threading import Lock
from time import monotonic

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.core.line_catalog import line_key
from app.services.tail_buffer_service import MOSCOW


# The staff directory has one subdivision per shift. Some portal lines have
# no dedicated subdivision (and two distinct lines share CSB code 5410).
DIVISION_LINES = {
    "линия булки": "Булка",
    "линия слойки": "Слойка",
    "линия хлеба": "Хлеба",
    "линия сухарей": "Сухари",
    "линия сэндвичей": "Сэндвичи",
    "линия жареных блюд": "Жареные блюда",
    "линия лазаньи": "Лазанья",
    "линия миквак": "Миквак",
    "линия бургеров": "Бургеры",
    "линия супов": "Супы",
    "линия салатов": "Салаты",
    "линия напитков": "Напитки",
}
ATTENDANCE_LINES = frozenset(line_key(name) for name in DIVISION_LINES.values())

STAFF = text("""
SELECT code, division FROM OPENQUERY([CONS-DB02], '
    SELECT CONVERT(nvarchar(100), code) AS code,
           CONVERT(nvarchar(255), StaffRoutineDivisions) AS division
    FROM MCC.IAS.v_FKTimeShift_Staff
')
""")
MOVEMENTS = text("""
SET NOCOUNT ON;
EXEC dbo.GetFKPersonalMovements :start, :end, NULL, NULL
""")


def employee_code(value):
    return str(value or "").strip().casefold()


def count_by_line(staff, movements, start, end):
    line_staff = defaultdict(set)
    for code, division in staff:
        name = DIVISION_LINES.get((division or "").partition(".")[0].strip().casefold())
        if name and employee_code(code):
            line_staff[line_key(name)].add(employee_code(code))

    arrivals = set()
    for row in movements:
        if (start <= row["RepDate"] < end
                and (row["TypePass"] or "").strip().upper() == "ВХОД"
                and "САНПРОПУСКНИК" in (row["AreaName"] or "").upper()
                and (row["TerminalName"] or "").rstrip().upper().endswith("-ВХОД")):
            arrivals.add(employee_code(row["Code"]))
    return {line: len(codes & arrivals) for line, codes in line_staff.items()}


def read_counts(day, url):
    start = datetime.combine(day, time.min)
    end = start + timedelta(days=1)
    # The procedure uses a linked server and cannot run inside a distributed transaction.
    engine = create_engine(make_url(url).set(database="TimeShiftFK"), isolation_level="AUTOCOMMIT",
                           connect_args={"login_timeout": 5, "timeout": 25})
    try:
        with engine.connect() as conn:
            staff = conn.execute(STAFF).all()
            movements = conn.execute(MOVEMENTS, {"start": start, "end": end}).mappings()
            return count_by_line(staff, movements, start, end)
    finally:
        engine.dispose()


_lock = Lock()
_cache_day: date | None = None
_expires = 0.0
_snapshot: dict = {}


def today_attendance():
    global _cache_day, _expires, _snapshot
    with _lock:
        day = datetime.now(MOSCOW).date()
        if day == _cache_day and monotonic() < _expires:
            return _snapshot
        url = (settings.skud_database_url or settings.production_eol_database_url
               or settings.production_fact_database_url).strip()
        counts = {}
        status = "unavailable"
        if url.startswith("mssql+pymssql://"):
            try:
                counts = read_counts(day, url)
                status = "ok"
            except SQLAlchemyError:
                pass
        _snapshot = {"date": day.isoformat(), "status": status, "counts": counts}
        _cache_day = day
        _expires = monotonic() + 60
        return _snapshot
