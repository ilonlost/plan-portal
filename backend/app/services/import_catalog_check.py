"""Check actual planning mappings, independently of line names in the workbook."""
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.models.entities import Product, LineCapability, ProductionLine

CATALOG_WARNING_PREFIX = "Справочник: "


def check_import_catalog(db, preview, line_id=None):
    preview.catalog_issues = []
    if preview.template_type not in {"ohl_daily", "quarter_weekly"}:
        return
    products = {p.sku: p for p in db.scalars(select(Product).options(
        joinedload(Product.capabilities).joinedload(LineCapability.line),
    )).unique()}
    selected = db.get(ProductionLine, line_id) if line_id else None
    grouped = {}
    for row in preview.rows:
        product = products.get(row.sku)
        reason = None
        if product is None:
            reason = "артикул отсутствует; не назначены производственная линия и скорость"
        elif not product.active or product.catalog_status != "active":
            reason = "артикул заблокирован или удалён"
        else:
            mappings = [c for c in product.capabilities if not line_id or c.line_id == line_id]
            active = [c for c in mappings if c.line and c.line.status == "active"]
            if not mappings:
                reason = f"нет привязки к линии «{selected.name}»" if selected else "не назначена производственная линия и скорость"
            elif not active:
                reason = "привязка только к неактивным линиям: " + ", ".join(sorted({c.line.name for c in mappings if c.line}))
            elif not any(c.units_per_hour and c.units_per_hour > 0 for c in active):
                reason = "не задана положительная скорость (кг/ч) на линиях: " + ", ".join(sorted({c.line.name for c in active}))
        row.warnings = [w for w in row.warnings if not w.startswith(CATALOG_WARNING_PREFIX)]
        if reason:
            warning = CATALOG_WARNING_PREFIX + reason
            row.warnings.insert(0, warning)
            key = (row.sku, reason)
            if key not in grouped:
                grouped[key] = {"sku": row.sku, "product_name": row.product_name, "line_hint": row.line_hint,
                                "reason": reason, "row_numbers": []}
            if row.row_number not in grouped[key]["row_numbers"]:
                grouped[key]["row_numbers"].append(row.row_number)
    preview.catalog_issues = list(grouped.values())
