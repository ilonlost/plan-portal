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


def test_maintenance_schedule_template_is_detected() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "График ТО"
    sheet.append(["Линия", "Дата", "Смена", "Тип события", "Длительность, ч", "Причина / работы"])
    sheet.append(["Жареные блюда", "14.09.2026", "День", "ТО", 2.5, "Замена подшипника"])
    sheet.append(["Супы", "15.09.2026", "Ночь", "Простой", 1, "Остановка CONS"])
    stream = BytesIO(); workbook.save(stream)

    preview = ExcelImportService().parse(stream.getvalue(), "График ТО.xlsx")

    assert preview.template_type == "maintenance_schedule"
    assert preview.valid_rows == 2
    assert preview.rows[0].event_kind == "maintenance"
    assert preview.rows[0].duration_hours == 2.5
    assert preview.rows[1].event_kind == "downtime"
    assert preview.rows[1].shift == "night"
