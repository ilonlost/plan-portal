from io import BytesIO

from openpyxl import Workbook

from app.services.import_service import ExcelImportService


def test_excel_import_preview_validates_rows() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Артикул", "Наименование", "Количество", "Желаемая дата", "Крайняя дата"])
    sheet.append(["SKU-1", "Продукт", 120, "17.08.2026", "18.08.2026"])
    sheet.append(["", "Ошибка", -5, "bad", "18.08.2026"])
    stream = BytesIO()
    workbook.save(stream)
    preview = ExcelImportService().parse(stream.getvalue(), "demand.xlsx")
    assert preview.total_rows == 2
    assert preview.valid_rows == 1
    assert preview.invalid_rows == 1

