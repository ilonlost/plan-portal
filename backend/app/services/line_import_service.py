"""Filtering and replacement boundaries shared by preview and confirmation."""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.core.line_catalog import line_key
from app.models.entities import LineCapability, Product, ProductionLine, ProductionScheduleItem
from app.services.task_date_service import ohl_production_date


def active_line(db, line_id):
    line = db.get(ProductionLine, line_id)
    if not line or line.status != "active":
        raise HTTPException(422, "Выберите действующую производственную линию")
    return line


def filter_line_preview(db, preview, line_id, start=None, end=None):
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
    if start is not None or end is not None:
        if not start or not end or end < start or (end - start).days > 92:
            raise HTTPException(422, "Выберите обе даты, не более 93 дней")
        products = {row.sku: row for row in db.scalars(select(Product))}
        selected = []
        for row in preview.rows:
            if not row.requested_date:
                continue
            if preview.template_type == "quarter_weekly":
                if row.requested_date > end or (row.due_date or row.requested_date) < start:
                    continue
                row.source_plan_date = row.source_plan_date or row.requested_date
                row.requested_date = max(row.requested_date, start)
                row.due_date = min(row.due_date or row.requested_date, end)
            else:
                day = row.requested_date
                if preview.template_type == "ohl_daily":
                    source = row.marking_date or row.source_plan_date or day
                    product = products.get(row.sku)
                    day = ohl_production_date(source, line, product)
                if not start <= day <= end:
                    continue
            selected.append(row)
        preview.rows = selected
        note = f"Период загрузки: {start:%d.%m.%Y} — {end:%d.%m.%Y}."
        if preview.template_type == "quarter_weekly":
            note += " ЗАМ: полный объём выбранных недель размещается внутри этого окна, без пропорционального уменьшения."
        if note not in preview.notes:
            preview.notes.append(note)
    preview.reference_rows = []
    preview.total_rows = len(preview.rows)
    preview.valid_rows = sum(row.valid for row in preview.rows)
    preview.invalid_rows = len(preview.rows) - preview.valid_rows
    if not any(note.startswith(f"Только линия «{line.name}»:") for note in preview.notes):
        preview.notes.append(f"Только линия «{line.name}»: отобрано {len(preview.rows)} из {total} строк. Остальные линии не изменяются.")
    if not preview.valid_rows:
        if start:
            raise HTTPException(422, f"Для линии «{line.name}» в выбранном периоде нет корректных строк. Проверьте даты файла, период и привязку артикулов.")
        raise HTTPException(422, f"В файле нет корректных строк для линии «{line.name}». Проверьте привязку артикула и положительную скорость в справочнике.")
    return preview


def replacement_tasks(plan, line_id, kind, start=None, end=None):
    return [row for row in plan.schedule_items if row.line_id == line_id and row.demand_item_id
            and row.schedule_kind == "production" and row.source_kind == kind and not row.excluded
            and (not start or (row.production_date and start <= row.production_date <= end))]


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
