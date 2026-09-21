"""Read-only CSB tail-buffer postings, using the owner's movement direction."""
from datetime import date, datetime, timedelta, timezone
from contextlib import contextmanager
from decimal import Decimal
import re

from sqlalchemy import create_engine, text

from app.core.config import settings

CENTERS = [
    ("KC", "5400", "Миквак"), ("KC", "5410", "Жареные блюда"),
    ("KC", "5420", "Салаты"), ("KC", "5430", "Супы"),
    ("KC", "5440", "Лазанья"), ("KC", "5460", "Бургеры"),
    ("KC", "5470", "Напитки"), ("KC", "5480", "Сэндвичи"),
    ("PC", "5800", "Слойка"), ("PC", "5810", "Булка"),
    ("PC", "5820", "Хлеб"), ("PC", "5830", "Ручная зона"),
]
PAGE_SIZE = 100
MOSCOW = timezone(timedelta(hours=3))
FIRST_MOVEMENT_SCOPE = "Первое движение SSCC по L52_REC_NR в доступных месяцах выбранного периода"


def month_tables(start: date, end: date) -> list[str]:
    day = start.replace(day=1)
    result = []
    while day <= end:
        result.append(f"cp_DWH_LA0052_{day:%Y%m}")
        day = (day.replace(day=28) + timedelta(days=4)).replace(day=1)
    return result


def movement_cte(schema: str, tables: list[str]) -> str:
    # SQL identifiers cannot be bind parameters: validate every one strictly.
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise ValueError("invalid schema")
    if not tables or any(not re.fullmatch(r"cp_DWH_LA0052_\d{6}", table) for table in tables):
        raise ValueError("invalid monthly table")
    movement = " UNION ALL ".join(
        f"SELECT L52_REC_NR, L52_NVE, L52_DATE_MOVE, L52_BW_TYP, L52_KST_NR_1, L52_KST_NR_2, L52_MENGE_LE, '{table[-6:]}' AS source_month FROM [{schema}].[{table}]"
        for table in tables
    )
    return f"""
WITH Movements AS ({movement}),
Normalized AS (
 SELECT *, TRY_CONVERT(decimal(28,6), L52_MENGE_LE) AS weight_kg, TRY_CONVERT(datetime2, CONVERT(varchar(40), L52_DATE_MOVE, 126), 112) AS moved_at,
 IIF(L52_BW_TYP IN (2,4), L52_KST_NR_1, L52_KST_NR_2) AS source_center,
 IIF(L52_BW_TYP IN (1,3), L52_KST_NR_1, L52_KST_NR_2) AS target_buffer
 FROM Movements
), Filtered AS (
 SELECT * FROM Normalized
 WHERE moved_at >= :start_at AND moved_at < :end_at
 AND ((source_center IN (5400,5410,5420,5430,5440,5460,5470,5480) AND target_buffer = :buffer_kc)
   OR (source_center IN (5800,5810,5820,5830) AND target_buffer = :buffer_pc))
)
"""


def build_query(schema: str, tables: list[str], *, export: bool = False, searching: bool = False) -> str:
    search_clause = f"""AND (CONVERT(nvarchar(80), L52_NVE) LIKE :search ESCAPE '~'
 OR EXISTS (SELECT 1 FROM [{schema}].[cp_DWH_SY8581] card
 WHERE card.SY8581_NVE = Filtered.L52_NVE AND card.SY8581_KST_NR <> 0
 AND (CONVERT(nvarchar(40), card.SY8581_ART_NR) LIKE :search ESCAPE '~'
 OR EXISTS (SELECT 1 FROM [{schema}].[cp_DWH_SY0012_SY8212_SY9014_SY9118] art
 WHERE art.SY0012_NR = card.SY8581_ART_NR AND art.SY0012_BEZ LIKE :search ESCAPE '~'))))""" if searching else ""
    paging = "" if export else "ORDER BY moved_at DESC, source_month DESC, L52_REC_NR DESC OFFSET :offset ROWS FETCH NEXT :limit ROWS ONLY"
    return movement_cte(schema, tables) + f""", Selected AS (
 SELECT *, COUNT_BIG(*) OVER () AS total_count FROM Filtered
 WHERE (:center = '' OR source_center = TRY_CONVERT(int, :center)) {search_clause}
 {paging}
), FirstRecords AS (
 SELECT m.L52_NVE, m.moved_at,
 ROW_NUMBER() OVER (PARTITION BY m.L52_NVE ORDER BY m.L52_REC_NR ASC, m.source_month ASC) AS rn
 FROM Normalized m WHERE EXISTS (SELECT 1 FROM Selected p WHERE p.L52_NVE = m.L52_NVE)
), Cards AS (
 SELECT s.SY8581_NVE, MIN(CONVERT(varchar(40), s.SY8581_ART_NR)) AS sku,
 COUNT(DISTINCT s.SY8581_ART_NR) AS article_count,
 MIN(TRY_CONVERT(date, CONVERT(varchar(8), s.SY8581_ANL_DATUM), 112)) AS created_date,
 MIN(s.SY8581_KST_NR) AS card_buffer
 FROM [{schema}].[cp_DWH_SY8581] s
 WHERE s.SY8581_KST_NR <> 0
 AND EXISTS (SELECT 1 FROM Selected p WHERE p.L52_NVE = s.SY8581_NVE)
 AND (CONVERT(varchar(40), s.SY8581_ART_NR) LIKE '101%' OR CONVERT(varchar(40), s.SY8581_ART_NR) LIKE '45%'
 OR CONVERT(varchar(40), s.SY8581_ART_NR) LIKE '46%' OR CONVERT(varchar(40), s.SY8581_ART_NR) LIKE '47%'
 OR CONVERT(varchar(40), s.SY8581_ART_NR) LIKE '35%' OR CONVERT(varchar(40), s.SY8581_ART_NR) LIKE '102%')
 GROUP BY s.SY8581_NVE
)
SELECT p.source_month, p.L52_REC_NR AS record_id, CONVERT(varchar(80),p.L52_NVE) AS sscc,
 p.source_center, p.target_buffer, p.moved_at, p.weight_kg, p.total_count,
 s.sku, s.article_count, s.created_date, s.card_buffer, f.moved_at AS first_moved_at,
 (SELECT MAX(CONVERT(nvarchar(500), a.SY0012_BEZ)) FROM [{schema}].[cp_DWH_SY0012_SY8212_SY9014_SY9118] a
  WHERE a.SY0012_NR = s.sku) AS product_name,
 (SELECT MAX(CONVERT(nvarchar(500), w.SY0315_KST_BEZ1)) FROM [{schema}].[cp_DWH_SY0315] w
  WHERE w.SY0315_NR = p.target_buffer) AS buffer_name
FROM Selected p LEFT JOIN Cards s ON s.SY8581_NVE = p.L52_NVE
LEFT JOIN FirstRecords f ON f.L52_NVE = p.L52_NVE AND f.rn = 1
ORDER BY p.moved_at DESC, p.source_month DESC, p.L52_REC_NR DESC
"""


def summary_query(schema: str, tables: list[str]) -> str:
    return movement_cte(schema, tables) + """, Buckets AS (
 SELECT *, DATEADD(minute, (DATEDIFF(minute, :start_at, moved_at) / :bucket_minutes) * :bucket_minutes, :start_at) AS bucket
 FROM Filtered
)
SELECT source_center, bucket, GROUPING(source_center) AS all_centers, GROUPING(bucket) AS is_total,
 COUNT_BIG(*) AS postings, COUNT(DISTINCT L52_NVE) AS sscc_count,
 SUM(weight_kg) AS weight_kg, SUM(CASE WHEN weight_kg IS NULL THEN 1 ELSE 0 END) AS missing_weight_count,
 MIN(moved_at) AS first_at, MAX(moved_at) AS last_at
FROM Buckets GROUP BY GROUPING SETS ((source_center, bucket), (source_center), ())
"""


class SourceError(Exception):
    def __init__(self, status: str, issues: list[str]):
        self.status, self.issues = status, issues


@contextmanager
def source_connection(start: date, end: date, *, timeout: int = 30):
    engine = None
    try:
        if not settings.production_fact_database_url.strip():
            raise SourceError("not_configured", ["Подключение DWH ещё не настроено"])
        if not settings.production_fact_database_url.startswith("mssql+pymssql://"):
            raise ValueError("driver")
        schema = settings.production_fact_db_schema
        wanted = month_tables(start, end)
        movement_cte(schema, wanted)
        buffers = [settings.production_fact_buffer_kc, settings.production_fact_buffer_pc]
        if any(not re.fullmatch(r"\d{1,9}", value) for value in buffers):
            raise ValueError("buffer")
        params = {"start_at": datetime.combine(start, datetime.min.time()),
                  "end_at": datetime.combine(end + timedelta(days=1), datetime.min.time()),
                  "buffer_kc": int(buffers[0]), "buffer_pc": int(buffers[1])}
        engine = create_engine(settings.production_fact_database_url, pool_pre_ping=True,
                               connect_args={"login_timeout": 8, "timeout": timeout})
        with engine.connect() as connection:
            tables, issues = [], []
            for table in wanted:
                if connection.execute(text("SELECT OBJECT_ID(:table_name)"), {"table_name": f"{schema}.{table}"}).scalar() is not None:
                    tables.append(table)
                else:
                    issues.append(f"Таблица {table} отсутствует или недоступна для чтения")
            if not tables:
                raise SourceError("unavailable", issues)
            yield connection, schema, tables, params, issues
    except SourceError:
        raise
    except ValueError:
        raise SourceError("invalid_configuration", ["Проверьте mssql+pymssql URL, схему и числовые коды буферов в ENV"]) from None
    except Exception:
        raise SourceError("unavailable", ["Не удалось прочитать DWH. Проверьте подключение, права SELECT и поля таблиц"]) from None
    finally:
        if engine is not None:
            engine.dispose()


def base_result(start: date, end: date) -> dict:
    centers = [{"workshop_code": workshop, "code": code, "name": name} for workshop, code, name in CENTERS]
    return {"source": "csb_dwh", "status": "not_configured", "range": {"start": start, "end": end, "days": (end - start).days + 1},
              "centers": centers, "rows": [], "issues": [], "total_count": 0,
              "buffers": {"KC": settings.production_fact_buffer_kc, "PC": settings.production_fact_buffer_pc},
              "generated_at": datetime.now(MOSCOW), "first_movement_scope": FIRST_MOVEMENT_SCOPE}


def normalize_row(raw) -> dict:
    row = dict(raw)
    code = str(int(row["source_center"]))
    for field in ("moved_at", "first_moved_at"):
        value = row.get(field)
        if isinstance(value, datetime) and value.tzinfo is None:
            row[field] = value.replace(tzinfo=MOSCOW)
    workshop, _, name = next(item for item in CENTERS if item[1] == code)
    for field in ("sku", "sscc", "product_name", "buffer_name"):
        if row.get(field) is not None:
            row[field] = str(row[field]).strip()
    row["weight_kg"] = row.get("weight_kg")
    warning = "" if row.get("sku") else "Карточка SSCC не найдена или артикул вне заданных префиксов"
    if (row.get("article_count") or 0) > 1:
        row["sku"] = row["product_name"] = None
        warning = "У SSCC несколько артикулов: требуется проверка"
    if row["weight_kg"] is None:
        warning = "; ".join(filter(None, [warning, "Вес L52_MENGE_LE не заполнен или некорректен"]))
    return {**row, "id": f"{row['source_month']}-{row['record_id']}", "source_center": code,
            "workshop_code": workshop, "line_name": name, "target_buffer": str(row["target_buffer"]), "warning": warning}


def filter_params(center: str = "", search: str = "") -> dict:
    # Literal substring search: user '%'/'_' must not become SQL wildcards.
    escaped = search.strip().replace("~", "~~").replace("%", "~%").replace("_", "~_").replace("[", "~[")
    return {"center": center, "search": f"%{escaped}%"}


def load_tail_buffers(start: date, end: date, *, offset: int = 0, limit: int = PAGE_SIZE, center: str = "", search: str = "") -> dict:
    result = {**base_result(start, end), "offset": offset, "limit": limit}
    try:
        with source_connection(start, end) as (connection, schema, tables, params, issues):
            rows = connection.execute(text(build_query(schema, tables, searching=bool(search.strip()))),
                {**params, **filter_params(center, search), "offset": offset, "limit": limit}).mappings().all()
        result["rows"] = [normalize_row(row) for row in rows]
        result["total_count"] = int(rows[0]["total_count"]) if rows else 0
        result.update(status="partial" if issues else "connected", issues=issues)
    except SourceError as error:
        result.update(status=error.status, issues=error.issues)
    return result


def load_summary(start: date, end: date) -> dict:
    result = base_result(start, end)
    minutes = 60 if (end - start).days < 7 else 1440
    result.update(bucket_minutes=minutes, sscc_count=0, active_centers=0, weight_kg=0, missing_weight_count=0)
    for center in result["centers"]:
        center.update(postings=0, sscc_count=0, weight_kg=0, missing_weight_count=0, first_at=None, last_at=None, buckets=[])
    try:
        with source_connection(start, end) as (connection, schema, tables, params, issues):
            rows = connection.execute(text(summary_query(schema, tables)), {**params, "bucket_minutes": minutes}).mappings().all()
        for row in rows:
            if row["all_centers"]:
                result.update(total_count=int(row["postings"]), sscc_count=int(row["sscc_count"]), weight_kg=row.get("weight_kg") if row["postings"] else 0, missing_weight_count=int(row.get("missing_weight_count", 0)))
                continue
            center = next(item for item in result["centers"] if item["code"] == str(int(row["source_center"])))
            aware = lambda value: value.replace(tzinfo=MOSCOW) if value else None
            if row["is_total"]:
                center.update(postings=int(row["postings"]), sscc_count=int(row["sscc_count"]), weight_kg=row.get("weight_kg"), missing_weight_count=int(row.get("missing_weight_count", 0)), first_at=aware(row["first_at"]), last_at=aware(row["last_at"]))
            else:
                center["buckets"].append({"at": aware(row["bucket"]), "postings": int(row["postings"]), "weight_kg": row.get("weight_kg"), "missing_weight_count": int(row.get("missing_weight_count", 0))})
        result.update(status="partial" if issues else "connected", issues=issues,
                      active_centers=sum(center["postings"] > 0 for center in result["centers"]))
    except SourceError as error:
        result.update(status=error.status, issues=error.issues)
    return result


def iter_export_rows(start: date, end: date, center: str = "", search: str = ""):
    # One query, no UI pagination/cap. Stream rows rather than keeping an entire half-year in memory.
    with source_connection(start, end, timeout=120) as (connection, schema, tables, params, issues):
        if issues:
            raise SourceError("partial", issues)
        cursor = connection.execute(text(build_query(schema, tables, export=True, searching=bool(search.strip()))),
                                    {**params, **filter_params(center, search)}).mappings()
        for row in cursor:
            yield normalize_row(row)


def group_cycles(rows):
    """Rows ordered by center/time. A gap of exactly five minutes starts a new cycle.

    Cycles describe posting activity inside the selected interval, not confirmed
    machine uptime. Negative movements retain their sign; unknown weight is counted.
    """
    current = None
    last = None
    for raw in rows:
        code, at, weight = str(int(raw["source_center"])), raw["moved_at"], raw["weight_kg"]
        gap = (at - last).total_seconds() if current and current["source_center"] == code else None
        if current is None or current["source_center"] != code or gap >= 300:
            if current is not None:
                yield current
            current = dict(source_center=code, start_at=at, end_at=at, postings=0,
                           weight_kg=None, missing_weight_count=0,
                           gap_before_minutes=Decimal(str(gap)) / 60 if gap is not None else None)
        current["end_at"] = at
        current["postings"] += 1
        if weight is None:
            current["missing_weight_count"] += 1
        else:
            current["weight_kg"] = (current["weight_kg"] or Decimal(0)) + Decimal(str(weight))
        last = at
    if current is not None:
        yield current


def iter_cycle_rows(start: date, end: date, center: str = ""):
    # All articles participate in boundaries, even when the detail table has a SKU search.
    with source_connection(start, end, timeout=120) as (connection, schema, tables, params, issues):
        if issues:
            raise SourceError("partial", issues)
        sql = movement_cte(schema, tables) + """
SELECT source_center, moved_at, weight_kg FROM Filtered
WHERE (:center = '' OR source_center = TRY_CONVERT(int, :center))
ORDER BY source_center, moved_at, source_month, L52_REC_NR
"""
        rows = connection.execute(text(sql), {**params, "center": center}).mappings()
        yield from group_cycles(rows)
