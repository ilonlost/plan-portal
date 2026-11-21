"""Read-only production report. No writes or fallback to buffer movements."""
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO

from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from sqlalchemy import create_engine, text

from app.core.config import settings
from app.services.production_fact_export import append_row
from app.services.tail_buffer_service import MOSCOW, SourceError

SOURCE = "mtz.PLAN_FACT_PRODUCTION_EOL"
MAX_ROWS = 100_000
QUERY = """
SELECT TOP (:max_rows)
 KA0133_PROD_KST_NR AS center, SY0315_KST_BEZ1 AS center_name,
 KA0133_PLAN_DATUM AS plan_date, KA0133_REZ_NR AS sku, sy0012_BEZ AS product_name,
 KA0133_VAR_NR AS variant, KA0133_PLAN_CHAR_GR AS plan_quantity,
 SY0012_EK_ME AS quantity_unit, KA0133_GES_MENGE_SGE AS plan_kg,
 KA0133_PROD_AUFTR_NR AS order_number, KA0133_PROD_AUFTR_LFD_NR AS order_line,
 KA0133_START_DATUM_SOLL AS planned_start_date,
 SY0012_EK_KGME AS kg_per_unit, SY0012_LA_ME AS storage_unit,
 L52_DAY_MENGE_LE AS day_storage, L52_DAY_MENGE_ME AS day_quantity,
 L52_NIGHT_MENGE_LE AS night_storage, L52_NIGHT_MENGE_ME AS night_quantity,
 L52_LAGERTYP_LOS AS lot, DATE_PRODUCTION AS production_date
FROM [mtz].[PLAN_FACT_PRODUCTION_EOL]
WHERE KA0133_PLAN_DATUM >= :start_plan AND KA0133_PLAN_DATUM < :end_plan
ORDER BY DATE_PRODUCTION DESC, KA0133_PROD_KST_NR, KA0133_REZ_NR,
 KA0133_PROD_AUFTR_NR, KA0133_PROD_AUFTR_LFD_NR, KA0133_VAR_NR, L52_LAGERTYP_LOS
"""
NOTE = ("Факт, кг = количество дня/ночи × кг на единицу. Пустое количество смены считается нулём. "
        "План относится ко всему заказу и может повторяться за несколько дат; не суммируйте его по строкам. "
        "Период, график и даты заданий — по плановой дате задания, как в BI. "
        "Факт включает все записи выпуска выбранных заданий; фактическая дата выпуска сохранена в XLSX.")


def read_source(start: date, end: date, query, normalize, label="факта производства"):
    url = settings.production_eol_database_url.strip()
    if not url:
        raise SourceError("not_configured", [f"Подключение {label} ещё не настроено"])
    if not url.startswith("mssql+pymssql://"):
        raise SourceError("invalid_configuration", ["Проверьте настройку подключения факта производства"])
    engine = None
    try:
        engine = create_engine(url, pool_pre_ping=True, connect_args={"login_timeout": 8, "timeout": 30})
        with engine.connect() as connection:
            rows = connection.execute(text(query), {
                "start_at": datetime.combine(start, datetime.min.time()),
                "end_at": datetime.combine(end + timedelta(days=1), datetime.min.time()),
                "start_plan": int(start.strftime("%Y%m%d")),
                "end_plan": int((end + timedelta(days=1)).strftime("%Y%m%d")),
                "max_rows": MAX_ROWS + 1,
            }).mappings().all()
        if len(rows) > MAX_ROWS:
            raise SourceError("unavailable", ["Слишком много строк. Сократите период отчёта"])
        return [normalize(row) for row in rows]
    except SourceError:
        raise
    except Exception:
        # Driver errors can contain the URL, SQL, server names and credentials.
        raise SourceError("unavailable", [f"Не удалось прочитать данные {label}. Проверьте подключение и права SELECT на таблицу источника"]) from None
    finally:
        if engine is not None:
            engine.dispose()


def normalize_row(raw):
    row = dict(raw)
    for field in ("center", "center_name", "sku", "product_name", "quantity_unit", "storage_unit", "order_number", "lot"):
        row[field] = str(row[field]).strip() if row.get(field) is not None else ""
    for field in ("plan_date", "planned_start_date"):
        try:
            row[field] = datetime.strptime(str(row[field]), "%Y%m%d").date()
        except (ValueError, TypeError):
            row[field] = None
    if isinstance(row["production_date"], datetime):
        row["production_date"] = row["production_date"].date()
    factor = row.get("kg_per_unit")
    valid_factor = factor is not None and factor > 0
    row["warning"] = "" if valid_factor else "Не задан положительный вес единицы; итог в кг неполный"
    for shift in ("day", "night"):
        quantity = row.get(f"{shift}_quantity") or Decimal(0)
        row[f"{shift}_kg"] = quantity * factor if valid_factor else None
    row["total_kg"] = row["day_kg"] + row["night_kg"] if valid_factor else None
    return row


def read_rows(start: date, end: date):
    return read_source(start, end, QUERY, normalize_production_row)


def normalize_production_row(raw):
    row = normalize_row(raw)
    row["report_date"] = row["plan_date"]
    if row["report_date"] is None:
        raise SourceError("unavailable", ["В источнике производства есть задание без корректной плановой даты"])
    return row


def report_date(row):
    return row.get("report_date", row["production_date"])


def totals(rows):
    def total(field):
        values = [row[field] for row in rows if row[field] is not None]
        return sum(values, Decimal(0)) if values or not rows else None
    return {"day_kg": total("day_kg"), "night_kg": total("night_kg"), "total_kg": total("total_kg"),
            "rows": len(rows), "missing_weight_rows": sum(row["total_kg"] is None for row in rows),
            "orders": len({(row["order_number"], row["order_line"]) for row in rows if row["order_number"]})}


def task_progress(plan, fact, conflicting_plan=False):
    reason = ""
    if conflicting_plan:
        reason = "Разные значения плана в источнике"
    elif plan is None or plan <= 0:
        reason = "Не задан положительный план"
    elif fact is None:
        reason = "Неполный вес факта"
    if reason:
        return {"status": "unknown", "status_reason": reason, "progress_percent": None}
    rounded = lambda value: value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return {"status": "completed" if rounded(fact) >= rounded(plan) else "pending",
            "status_reason": "", "progress_percent": rounded(fact / plan * 100)}


def task_totals(rows):
    groups = defaultdict(list)
    for row in rows:
        groups[(row["order_number"], row["order_line"], row["sku"], row["center"])].append(row)
    tasks = []
    for (order_number, order_line, sku, center), items in sorted(groups.items()):
        # Plan is repeated for every production date/lot, never additive here.
        plans = {row["plan_kg"] for row in items}
        plan = next(iter(plans)) if len(plans) == 1 else None
        summary = totals(items)
        fact = summary["total_kg"] if not summary["missing_weight_rows"] else None
        daily = defaultdict(list)
        for row in items:
            daily[report_date(row)].append(row)
        fact_by_day = []
        for day, day_rows in sorted(daily.items()):
            day_summary = totals(day_rows)
            day_fact = day_summary["total_kg"] if not day_summary["missing_weight_rows"] else None
            fact_by_day.append({"date": day, "fact_kg": day_fact,
                                **task_progress(plan, day_fact, len(plans) > 1)})
        tasks.append({"order_number": order_number, "order_line": order_line, "sku": sku,
                      "center": center, "center_name": items[0]["center_name"],
                      "product_name": items[0]["product_name"], "plan_kg": plan, "fact_kg": fact,
                      "fact_by_day": fact_by_day,
                      **task_progress(plan, fact, len(plans) > 1)})
    return tasks


def report(start, end, center="", search=""):
    return build_report(read_rows(start, end), start, end, center, search)


def build_report(rows, start, end, center="", search="", *, source=SOURCE, note=NOTE,
                 title="Факт производства", date_title="Плановая дата задания"):
    centers = sorted({(row["center"], row["center_name"]) for row in rows})
    needle = search.strip().casefold()
    rows = [row for row in rows if (not center or row["center"] == center) and
            (not needle or any(needle in row[field].casefold() for field in ("sku", "product_name", "order_number", "lot")))]
    by_center, by_day = defaultdict(list), defaultdict(list)
    for row in rows:
        by_center[(row["center"], row["center_name"])].append(row)
        by_day[report_date(row)].append(row)
    return {"source": source, "status": "connected", "range": {"start": start, "end": end},
            "title": title, "date_title": date_title,
            "generated_at": datetime.now(MOSCOW), "note": note, "rows": rows, "total_count": len(rows),
            "centers": [{"code": code, "name": name} for code, name in centers], "summary": totals(rows),
            "by_task": task_totals(rows),
            "by_center": [{"code": code, "name": name, **totals(items)} for (code, name), items in sorted(by_center.items())],
            "by_day": [{"date": day, **totals(items)} for day, items in sorted(by_day.items())]}


def export_xlsx(data):
    book = Workbook(write_only=True)
    def sheet(name, headers, rows, widths=None):
        ws = book.create_sheet(name)
        ws.freeze_panes = "A2"
        for index, header in enumerate(headers, 1):
            ws.column_dimensions[get_column_letter(index)].width = widths[index - 1] if widths else 24
        append_row(ws, headers, header=True)
        count = 1
        for values in rows:
            append_row(ws, values)
            count += 1
        ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{count}"
    sheet("Сводка", ["Показатель", "Значение"], [
        ["Начало периода", data["range"]["start"]], ["Конец периода", data["range"]["end"]],
        ["Место затрат", data.get("filter_center") or "Все"], ["Поиск", data.get("filter_search") or "—"],
        ["Факт дня, кг", data["summary"]["day_kg"]], ["Факт ночи, кг", data["summary"]["night_kg"]],
        ["Итого, кг", data["summary"]["total_kg"]], ["Строк без веса", data["summary"]["missing_weight_rows"]],
        ["Источник", data["source"]], ["Правила расчёта", data["note"]]], [30, 100])
    sheet("По линиям", ["МЗ", "Линия", "День, кг", "Ночь, кг", "Итого, кг", "Строк без веса"],
          ([r[k] for k in ("code", "name", "day_kg", "night_kg", "total_kg", "missing_weight_rows")] for r in data["by_center"]))
    sheet("По датам", [data["date_title"], "День, кг", "Ночь, кг", "Итого, кг", "Строк без веса"],
          ([r[k] for k in ("date", "day_kg", "night_kg", "total_kg", "missing_weight_rows")] for r in data["by_day"]))
    fields = ["production_date", "center", "center_name", "sku", "product_name", "order_number", "order_line", "variant", "lot",
              "plan_date", "planned_start_date", "plan_quantity", "quantity_unit", "plan_kg", "kg_per_unit",
              "day_quantity", "night_quantity", "day_storage", "night_storage", "storage_unit", "day_kg", "night_kg", "total_kg", "warning"]
    detail_date_title = data.get("detail_date_title", "Дата фактического выпуска" if data["source"] == SOURCE else data["date_title"])
    sheet(data["title"], [detail_date_title, "МЗ", "Линия", "Артикул", "Продукция", "Заказ", "Позиция заказа", "Вариант", "Партия",
          "Дата плана", "Плановое начало", "План заказа, ед.", "Ед.", "План заказа, кг", "Кг на единицу", "День, ед.", "Ночь, ед.",
          "День, склад. ед.", "Ночь, склад. ед.", "Склад. ед.", "День, кг", "Ночь, кг", "Итого, кг", "Проверка"],
          ([row.get(key) for key in fields] for row in data["rows"]))
    output = BytesIO()
    book.save(output)
    return output.getvalue()
