"""Filtering and replacement boundaries shared by preview and confirmation."""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.core.line_catalog import line_key
from app.models.entities import LineCapability, Product, ProductionLine


def active_line(db, line_id):
    line = db.get(ProductionLine, line_id)
    if not line or line.status != "active":
        raise HTTPException(422, "Выберите действующую производственную линию")
    return line


def filter_line_preview(db, preview, line_id):
    line = active_line(db, line_id)
    if preview.template_type not in {"ohl_daily", "quarter_weekly", "maintenance_schedule"}:
        raise HTTPException(422, "Для одной линии поддерживаются стандартные файлы ОХЛ, ЗАМ и график ТО. Справочники загружайте в общем разделе.")
    skus = set(db.scalars(select(Product.sku).join(LineCapability).where(
        LineCapability.line_id == line_id, Product.active.is_(True), Product.catalog_status == "active",
        LineCapability.units_per_hour > 0,
    )))
    total = len(preview.rows)
    preview = preview.model_copy(deep=True)
    preview.rows = [row for row in preview.rows if (
        (row.line_hint and line_key(row.line_hint) == line_key(line.name))
        if preview.template_type == "maintenance_schedule" else
        row.sku in skus and (not row.line_hint or line_key(row.line_hint) == line_key(line.name))
    )]
    preview.reference_rows = []
    preview.total_rows = len(preview.rows)
    preview.valid_rows = sum(row.valid for row in preview.rows)
    preview.invalid_rows = len(preview.rows) - preview.valid_rows
    if not any(note.startswith(f"Только линия «{line.name}»:") for note in preview.notes):
        preview.notes.append(f"Только линия «{line.name}»: отобрано {len(preview.rows)} из {total} строк. Остальные линии не изменяются.")
    if not preview.valid_rows:
        raise HTTPException(422, f"В файле нет корректных строк для линии «{line.name}». Проверьте привязку артикула и положительную скорость в справочнике.")
    return preview


def demands_for_line(db, plan, demands, line_id):
    """Use actual placement first, including deletion tombstones; never steal other lines' tasks."""
    placements = {}
    for row in plan.schedule_items:
        if row.schedule_kind == "production" and row.demand_item_id and row.line_id:
            placements.setdefault(row.demand_item_id, set()).add(row.line_id)
    capabilities = {}
    for cap in db.scalars(select(LineCapability).join(LineCapability.line).where(
        ProductionLine.status == "active",
    ).options(joinedload(LineCapability.line))):
        capabilities.setdefault(cap.product_id, []).append(cap)
    selected = []
    for demand in demands:
        placed = placements.get(demand.id, set())
        if placed:
            if line_id not in placed:
                continue
            if len(placed) > 1:
                raise HTTPException(409, f"Артикул {demand.sku} разделён между несколькими линиями. Сначала объедините его задания на одной линии, затем повторите загрузку.")
        else:
            preferred = (demand.raw_data or {}).get("preferred_line_id")
            options = sorted(capabilities.get(demand.product_id, []), key=lambda cap: (cap.line.priority, cap.line_id))
            assigned = preferred or (options[0].line_id if options else None)
            if assigned != line_id:
                continue
        selected.append(demand)
    return selected
