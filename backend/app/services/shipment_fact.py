"""Historical shipment facts matching the BI working plan/fact report."""
from app.services import production_eol

SOURCE = "mtz.PLAN_FACT_PRODUCTION_PERIOD"
QUERY = """
SELECT TOP (:max_rows)
 PFP_PROD_KST AS center, PFP_KST_BEZ AS center_name,
 PFP_PLAN_DATUM AS plan_date, PFP_REZ_NR AS sku, PFP_REZ_BEZ AS product_name,
 PFP_VAR_NR AS variant, PFP_PLAN_CHAR_GR AS plan_quantity,
 PFP_EK_ME AS quantity_unit, PFP_GES_MENGE_SGE AS plan_kg,
 PFP_PROD_AUFTR_NR AS order_number, PFP_PROD_AUFTR_LFD_NR AS order_line,
 PFP_START_DATUM_SOLL AS planned_start_date,
 PFP_EK_KGME AS kg_per_unit, PFP_LA_ME AS storage_unit,
 PFP_DAY_MENGE_LE AS day_storage, PFP_DAY_MENGE_ME AS day_quantity,
 PFP_NIGHT_MENGE_LE AS night_storage, PFP_NIGHT_MENGE_ME AS night_quantity,
 PFP_LAGERTYP_LOS AS lot, PFP_DATE_PRODUCTION AS production_date
FROM [mtz].[PLAN_FACT_PRODUCTION_PERIOD]
WHERE PFP_PLAN_DATUM >= :start_plan AND PFP_PLAN_DATUM < :end_plan
ORDER BY PFP_PLAN_DATUM DESC, PFP_DATE_PRODUCTION DESC, PFP_PROD_KST, PFP_REZ_NR,
 PFP_PROD_AUFTR_NR, PFP_PROD_AUFTR_LFD_NR, PFP_VAR_NR, PFP_LAGERTYP_LOS
"""
NOTE = ("Исторический факт отгрузки — по данным отчёта BI «ПЛАН-ФАКТ рабочий». "
        "Период, график и даты заданий — по плановой дате задания. "
        "Факт, кг = количество дня/ночи × вес единицы из источника. "
        "План задания учитывается один раз; пустое количество смены считается нулём.")


def normalize_row(raw):
    return production_eol.normalize_production_row(raw)


def read_rows(start, end):
    return production_eol.read_source(start, end, QUERY, normalize_row, "факта отгрузки")


def report(start, end, center="", search=""):
    data = production_eol.build_report(read_rows(start, end), start, end, center, search,
                                      source=SOURCE, note=NOTE, title="Факт отгрузки",
                                      date_title="Плановая дата задания")
    data["detail_date_title"] = "Фактическая дата из источника"
    return data
