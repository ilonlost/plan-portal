from datetime import date
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook

from app.services.import_service import ExcelImportService


def workbook_bytes(workbook: Workbook) -> bytes:
    stream = BytesIO()
    workbook.save(stream)
    return stream.getvalue()


def test_ohl_template_preserves_date_and_keeps_source_kilograms() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "ОХЛ"
    sheet.append([None, None, None, None, None, None, "План производства"])
    sheet.append(["Сегмент", "Статус", "СГ", "Маркировка", "SAP-код", "Наименование", "12 авг"])
    sheet.append(["ОХЛ", "По графику", 10, "Нет", 101, "Курица 150г*4 (0,6кг)", Decimal("5")])
    ref = workbook.create_sheet("Справочник ФК")
    ref.append(["Код", "Наименование", "Линия", "Категория", "Скорость"])
    ref.append([101, "Курица", "Миквак", "Кулинария", 240])

    preview = ExcelImportService().parse(workbook_bytes(workbook), "ОХЛ 2026.xlsx")
    row = preview.rows[0]
    assert preview.template_type == "ohl_daily"
    assert row.requested_date == row.due_date == date(2026, 8, 12)
    assert row.source_quantity == Decimal("5")
    assert row.source_unit == "кг"
    assert row.quantity_kg == Decimal("5")
    assert row.box_count == Decimal("8.333")
    assert not row.warnings


def test_reference_template_reads_speed_batch_and_restriction() -> None:
    workbook = Workbook()
    workbook.active.title = "ПЦ"
    workbook.create_sheet("КЦ 1")
    workbook.create_sheet("КЦ 2")
    ref = workbook.create_sheet("Справочник")
    ref.append(["Код", "Наименование", "Состояние", "Линия", "Категория", "Кратко", "Код", "Скорость", "Код", "Замес", "Тип", "Расчёт", "Статус", "Чел", "Чел-ч", "Мин заказ", "Ограничение"])
    ref.append([101, "Круассан 70г*70 (4,9кг)", "ЗАМ", "Слойка", "ПЦ", "Круассан", 101, 462, 101, Decimal("273.5686"), "Дежа", None, "Активный", 10, None, Decimal("820.7058"), "Расстойка"])

    preview = ExcelImportService().parse(workbook_bytes(workbook), "План производства 12.08.2026.xlsm")
    row = preview.rows[0]
    assert preview.template_type == "production_reference"
    assert row.speed_kg_hour == Decimal("462")
    assert row.batch_quantum_kg == Decimal("273.5686")
    assert row.units_per_box == Decimal("70.000")
    assert row.restrictions == "Расстойка"


def test_sandwich_advance_marking_moves_production_one_day_back() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "ОХЛ"
    sheet.append([None, None, None, None, None, None, "План производства"])
    sheet.append(["Сегмент", "Статус", "СГ", "Маркировка", "SAP-код", "Наименование", "12 авг"])
    sheet.append(["ОХЛ", "АЗ", 10, "Да", 101, "Сэндвич 150г*4 (0,6кг)", Decimal("4")])
    ref = workbook.create_sheet("Справочник ФК")
    ref.append(["Код", "Наименование", "Линия", "Категория", "Скорость"])
    ref.append([101, "Сэндвич", "Сэндвичи", "Кулинария", 240])

    row = ExcelImportService().parse(workbook_bytes(workbook), "ОХЛ 2026.xlsx").rows[0]
    assert row.advance_marking is True
    assert row.requested_date == date(2026, 8, 11)
    assert row.marking_date == date(2026, 8, 12)


def test_zam_reads_plain_week_numbers_from_upper_header_row() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "План ЗАМ"
    sheet.append([None, None, 36, 37, 38, 39])
    sheet.append(["SAP-код", "Наименование", None, None, None, None])
    sheet.append([101, "Круассан 70г*70 (4,9кг)", 10, None, 30, 40])

    preview = ExcelImportService().parse(workbook_bytes(workbook), "План ЗАМ 2026.xlsx")

    assert preview.template_type == "quarter_weekly"
    assert [(row.production_week, row.quantity) for row in preview.rows] == [
        (36, Decimal("10")),
        (38, Decimal("30")),
        (39, Decimal("40")),
    ]
    assert preview.rows[0].requested_date == date(2026, 8, 31)
    assert preview.rows[-1].due_date == date(2026, 9, 27)


def test_zam_monthly_totals_are_not_years_or_week_columns() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "План ЗАМ"
    sheet.append([None, None, None, 36, 37, 420, 2000, 3292, 39, 7677.6])
    sheet.append(["Сегмент", "Код товара SAP", "Название", "Сентябрь 2026", "36w", "37w", "38w", "39w", "Октябрь 2026", "Ноябрь 2026"])
    sheet.append(["ЗАМ", 1010019422, "Ангус бургер", 6480, 2000, 1600, 1600, 1280, 2160, 4320])
    for filename in ("ПЛАН_КВАРТАЛ ФК (корр. 17.09.26).xlsx", "План ЗАМ.xlsx"):
        preview = ExcelImportService().parse(workbook_bytes(workbook), filename)
        assert preview.valid_rows == 4
        assert [(row.production_week, row.quantity) for row in preview.rows] == [
            (36, Decimal("2000")), (37, Decimal("1600")),
            (38, Decimal("1600")), (39, Decimal("1280")),
        ]
        assert preview.rows[0].requested_date == date(2026, 8, 31)
        assert preview.rows[-1].due_date == date(2026, 9, 27)
        assert sum(row.quantity for row in preview.rows) == Decimal("6480")


def test_zam_numeric_totals_cannot_be_used_as_upper_week_headers() -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "План ЗАМ"
    sheet.append([None, None, 36, 37])
    sheet.append(["Артикул", "Наименование", "Сентябрь 2026", "Октябрь 2026"])
    sheet.append([101, "Продукция", 100, 200])
    import pytest
    with pytest.raises(ValueError, match="не найдены недельные колонки"):
        ExcelImportService().parse(workbook_bytes(workbook), "План.xlsx")
