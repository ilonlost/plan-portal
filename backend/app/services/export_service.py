from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill


class ExcelExportService:
    HEADERS = [
        "№", "Дата", "Смена", "Линия", "Тип записи", "Продукт", "Артикул",
        "Количество источника", "Ед. источника", "Задание, шт.", "Задание, кг", "Коробов", "Квантов",
        "Время, ч", "Загрузка смены, %", "Причина", "Статус", "Источник", "Предупреждения",
    ]

    def build(self, items: list[dict]) -> bytes:
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Производственный план"
        sheet.append(self.HEADERS)
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="C8102E")
            cell.alignment = Alignment(horizontal="center")
        for item in items:
            sheet.append([
                item.get("sequence"), item.get("production_date"), "Ночь" if item.get("shift") == "night" else "День",
                item.get("line_name") or "—", item.get("schedule_kind"), item.get("product_name"), item.get("sku"),
                self._float(item.get("source_quantity")), item.get("source_unit"),
                self._float(item.get("quantity_units")), self._float(item.get("quantity_kg") or item.get("quantity")), self._float(item.get("box_count")),
                self._float(item.get("batch_count")), float(item.get("required_hours", 0)),
                float(item.get("load_percent", 0)), item.get("reason"), item.get("status"),
                item.get("source"), "; ".join(item.get("warnings", [])),
            ])
            status = item.get("status")
            color = "FCE8EC" if status in {"conflict", "unscheduled"} else "FFF5DF" if status == "warning" else "EAF8F1"
            for cell in sheet[sheet.max_row]:
                cell.fill = PatternFill("solid", fgColor=color)
        widths = [8, 14, 10, 24, 16, 30, 16, 20, 14, 16, 16, 12, 12, 14, 20, 30, 16, 14, 44]
        for index, width in enumerate(widths, start=1):
            sheet.column_dimensions[sheet.cell(1, index).column_letter].width = width
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        stream = BytesIO()
        workbook.save(stream)
        return stream.getvalue()

    @staticmethod
    def _float(value):
        return float(value) if value is not None else None
