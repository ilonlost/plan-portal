"""Application XLSX export using the portal's existing openpyxl dependency."""
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from app.services.tail_buffer_service import CENTERS, FIRST_MOVEMENT_SCOPE, MOSCOW, iter_export_rows

MAX_DATA_ROWS = 1_048_575
HEADERS = ["Дата и время проводки (МСК)", "Цех", "МЗ", "Линия", "Артикул", "Продукция",
           "Дата создания SSCC", "Буфер назначения", "Название буфера", "SSCC",
           "Первое движение в выбранных месяцах (МСК)", "Буфер карточки SSCC", "Номер проводки", "Месяц источника", "Проверка"]
WIDTHS = [25, 9, 12, 24, 18, 56, 22, 19, 35, 26, 30, 22, 22, 19, 60]
CELL_ALIGNMENT = Alignment(vertical="top", wrap_text=True)
HEADER_FILL = PatternFill("solid", fgColor="C8102E")
HEADER_FONT = Font(color="FFFFFF", bold=True)


def append_row(sheet, values, header=False):
    cells = []
    for value in values:
        if isinstance(value, datetime) and value.tzinfo:
            value = value.astimezone(MOSCOW).replace(tzinfo=None)
        cell = WriteOnlyCell(sheet, value=value)
        if isinstance(value, str):
            cell.value = ILLEGAL_CHARACTERS_RE.sub("", value)
            cell.data_type = "s"  # Text identifiers and untrusted source text must never become formulas.
            cell.number_format = "@"
        elif isinstance(value, datetime):
            cell.number_format = "dd.mm.yyyy hh:mm:ss"
        elif isinstance(value, date):
            cell.number_format = "dd.mm.yyyy"
        elif isinstance(value, int):
            cell.number_format = "#,##0"
        cell.alignment = CELL_ALIGNMENT
        if header:
            cell.fill = HEADER_FILL
            cell.font = HEADER_FONT
        cells.append(cell)
    sheet.append(cells)


def export_workbook(start: date, end: date, center: str = "", search: str = "") -> str:
    workbook = Workbook(write_only=True)
    summary = workbook.create_sheet("Сводка")
    for letter, width in [("A", 40), ("B", 85), ("C", 24), ("D", 18)]:
        summary.column_dimensions[letter].width = width
    summary.freeze_panes = "A2"
    summary.row_dimensions[1].height = 28
    for index in (9, 10, 11):
        summary.row_dimensions[index].height = 48
    file = NamedTemporaryFile(prefix="plan-fact-", suffix=".xlsx", delete=False)
    path = file.name
    file.close()
    counts = Counter()
    warnings = 0
    sheet = None
    sheet_count = row_count = total = 0
    try:
        for row in iter_export_rows(start, end, center, search):
            if sheet is None or row_count >= MAX_DATA_ROWS:
                if sheet is not None:
                    sheet.auto_filter.ref = f"A1:O{row_count + 1}"
                sheet_count += 1
                sheet = workbook.create_sheet("Проводки" if sheet_count == 1 else f"Проводки {sheet_count}")
                sheet.freeze_panes = "A2"
                sheet.row_dimensions[1].height = 58
                sheet.sheet_format.defaultRowHeight = 32
                for index, width in enumerate(WIDTHS, 1):
                    sheet.column_dimensions[get_column_letter(index)].width = width
                append_row(sheet, HEADERS, header=True)
                row_count = 0
            append_row(sheet, [row["moved_at"], "ПЦ" if row["workshop_code"] == "PC" else "КЦ",
                row["source_center"], row["line_name"], row["sku"], row["product_name"], row["created_date"],
                row["target_buffer"], row["buffer_name"], row["sscc"], row["first_moved_at"],
                str(row["card_buffer"]) if row["card_buffer"] is not None else None,
                str(row["record_id"]), row["source_month"], row["warning"]])
            counts[row["source_center"]] += 1
            warnings += bool(row["warning"])
            row_count += 1
            total += 1
        if sheet is None:
            sheet = workbook.create_sheet("Проводки")
            for index, width in enumerate(WIDTHS, 1):
                sheet.column_dimensions[get_column_letter(index)].width = width
            sheet.freeze_panes = "A2"
            sheet.row_dimensions[1].height = 58
            append_row(sheet, HEADERS, header=True)
        sheet.auto_filter.ref = f"A1:O{row_count + 1}"
        append_row(summary, ["Факт хвостовых буферов", "CSB DWH"], header=True)
        for values in (["Начало периода", start], ["Конец периода включительно", end],
                       ["Место затрат", center or "Все МЗ"], ["Поиск", search or "Не задан"],
                       ["Сформировано (МСК)", datetime.now(MOSCOW)], ["Проводок выгружено", total],
                       ["Строк с предупреждениями", warnings], ["Первое движение", FIRST_MOVEMENT_SCOPE],
                       ["Единицы", "Проводки и SSCC. Часы работы, паузы и количество продукции из этих данных не определяются."],
                       ["Источник", "CSB_FK_REP: cp_DWH_LA0052_YYYYMM, cp_DWH_SY8581, cp_DWH_SY0012_SY8212_SY9014_SY9118, cp_DWH_SY0315"], []):
            append_row(summary, values)
        append_row(summary, ["Цех", "Линия", "МЗ", "Проводок"], header=True)
        for workshop, code, name in CENTERS:
            if not center or center == code:
                append_row(summary, ["ПЦ" if workshop == "PC" else "КЦ", name, code, counts[code]])
        workbook.save(path)
        return path
    except Exception:
        for sheet in workbook.worksheets:
            if not sheet.closed:
                sheet.close()
        # Saving closes/removes openpyxl's temporary worksheet XML files as well.
        try:
            workbook.save(path)
        except Exception:
            pass
        workbook.close()
        Path(path).unlink(missing_ok=True)
        raise
