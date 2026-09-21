"""Read-only CSB tail-buffer postings, using the owner's movement direction."""
from datetime import date, datetime, timedelta, timezone
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
LIMIT = 5000


def month_tables(start: date, end: date) -> list[str]:
    day = start.replace(day=1)
    result = []
    while day <= end:
        result.append(f"cp_DWH_LA0052_{day:%Y%m}")
        day = (day.replace(day=28) + timedelta(days=4)).replace(day=1)
    return result


def build_query(schema: str, tables: list[str]) -> str:
    # SQL identifiers cannot be bind parameters: validate every one strictly.
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema):
        raise ValueError("invalid schema")
    if not tables or any(not re.fullmatch(r"cp_DWH_LA0052_\d{6}", table) for table in tables):
        raise ValueError("invalid monthly table")
    movement = " UNION ALL ".join(
        f"SELECT L52_REC_NR, L52_NVE, L52_DATE_MOVE, L52_BW_TYP, L52_KST_NR_1, L52_KST_NR_2, '{table[-6:]}' AS source_month FROM [{schema}].[{table}]"
        for table in tables
    )
    return f"""
WITH Movements AS ({movement}),
Normalized AS (
 SELECT *, TRY_CONVERT(datetime2, CONVERT(varchar(40), L52_DATE_MOVE, 126), 112) AS moved_at,
 IIF(L52_BW_TYP IN (2,4), L52_KST_NR_1, L52_KST_NR_2) AS source_center,
 IIF(L52_BW_TYP IN (1,3), L52_KST_NR_1, L52_KST_NR_2) AS target_buffer
 FROM Movements
), Selected AS (
 SELECT TOP (:limit) *, COUNT_BIG(*) OVER () AS total_count FROM Normalized
 WHERE moved_at >= :start_at AND moved_at < :end_at
 AND ((source_center IN (5400,5410,5420,5430,5440,5460,5470,5480) AND target_buffer = :buffer_kc)
   OR (source_center IN (5800,5810,5820,5830) AND target_buffer = :buffer_pc))
 ORDER BY moved_at DESC, source_month DESC, L52_REC_NR DESC
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
 p.source_center, p.target_buffer, p.moved_at, p.total_count,
 s.sku, s.article_count, s.created_date, s.card_buffer, f.moved_at AS first_moved_at,
 (SELECT MAX(CONVERT(nvarchar(500), a.SY0012_BEZ)) FROM [{schema}].[cp_DWH_SY0012_SY8212_SY9014_SY9118] a
  WHERE CONVERT(varchar(40), a.SY0012_NR) = s.sku) AS product_name,
 (SELECT MAX(CONVERT(nvarchar(500), w.SY0315_KST_BEZ1)) FROM [{schema}].[cp_DWH_SY0315] w
  WHERE w.SY0315_NR = p.target_buffer) AS buffer_name
FROM Selected p LEFT JOIN Cards s ON s.SY8581_NVE = p.L52_NVE
LEFT JOIN FirstRecords f ON f.L52_NVE = p.L52_NVE AND f.rn = 1
ORDER BY p.moved_at DESC, p.source_month DESC, p.L52_REC_NR DESC
"""


def load_tail_buffers(start: date, end: date) -> dict:
    centers = [{"workshop_code": workshop, "code": code, "name": name} for workshop, code, name in CENTERS]
    result = {"source": "csb_dwh", "status": "not_configured", "range": {"start": start, "end": end},
              "centers": centers, "rows": [], "issues": [], "total_count": 0, "truncated": False,
              "buffers": {"KC": settings.production_fact_buffer_kc, "PC": settings.production_fact_buffer_pc},
              "first_movement_scope": "Первое движение SSCC по L52_REC_NR в доступных месяцах выбранного периода"}
    if not settings.production_fact_database_url.strip():
        result["issues"].append("Подключение DWH ещё не настроено")
        return result
    engine = None
    try:
        if not settings.production_fact_database_url.startswith("mssql+pymssql://"):
            raise ValueError("driver")
        schema = settings.production_fact_db_schema
        wanted = month_tables(start, end)
        build_query(schema, wanted)
        buffers = {}
        for workshop, value in result["buffers"].items():
            if value and not re.fullmatch(r"\d{1,9}", value):
                raise ValueError("buffer")
            buffers[workshop] = int(value) if value else None
            if not value:
                result["issues"].append(f"Не задан буфер назначения для {'ПЦ' if workshop == 'PC' else 'КЦ'}")
        engine = create_engine(settings.production_fact_database_url, pool_pre_ping=True,
                               connect_args={"login_timeout": 8, "timeout": 30})
        with engine.connect() as connection:
            tables = []
            for table in wanted:
                if connection.execute(text("SELECT OBJECT_ID(:table_name)"), {"table_name": f"{schema}.{table}"}).scalar() is not None:
                    tables.append(table)
                else:
                    result["issues"].append(f"Таблица {table} отсутствует или недоступна для чтения")
            if not tables:
                result["status"] = "unavailable"
                return result
            rows = connection.execute(text(build_query(schema, tables)), {
                "start_at": datetime.combine(start, datetime.min.time()),
                "end_at": datetime.combine(end + timedelta(days=1), datetime.min.time()),
                "buffer_kc": buffers["KC"], "buffer_pc": buffers["PC"], "limit": LIMIT,
            }).mappings().all()
        lookup = {code: (workshop, name) for workshop, code, name in CENTERS}
        for raw in rows:
            row = dict(raw)
            code = str(int(row["source_center"]))
            for field in ("moved_at", "first_moved_at"):
                value = row.get(field)
                if isinstance(value, datetime) and value.tzinfo is None:
                    row[field] = value.replace(tzinfo=timezone(timedelta(hours=3)))
            workshop, name = lookup[code]
            warning = "" if row.get("sku") else "Карточка SSCC не найдена или артикул вне заданных префиксов"
            if (row.get("article_count") or 0) > 1:
                row["sku"] = row["product_name"] = None
                warning = "У SSCC несколько артикулов: требуется проверка"
            result["rows"].append({**row, "id": f"{row['source_month']}-{row['record_id']}",
                                   "source_center": code, "workshop_code": workshop, "line_name": name,
                                   "target_buffer": str(row["target_buffer"]), "warning": warning})
        result["total_count"] = int(rows[0]["total_count"]) if rows else 0
        result["truncated"] = result["total_count"] > LIMIT
        result["status"] = "partial" if result["issues"] else "connected"
    except ValueError:
        result["status"] = "invalid_configuration"
        result["issues"].append("Проверьте mssql+pymssql URL, схему и числовые коды буферов в ENV")
    except Exception:
        result["status"] = "unavailable"
        result["rows"] = []
        result["issues"].append("Не удалось прочитать DWH. Проверьте подключение, права SELECT и поля таблиц")
    finally:
        if engine is not None:
            engine.dispose()
    return result
