"""Offer PF expansion for the actual production splits of one uploaded file."""
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import aliased, joinedload

from app.models.entities import DemandItem, PortalSetting, ProductionScheduleItem
from app.services.pf_mapping_service import PREFIX
from app.services.pf_service import preview_expansion, source_data


def component_index(db):
    index = {}
    for row in source_data()["rows"]:
        components = index.setdefault((row["gp_sku"], None), {})
        for kind in ("pf", "filling"):
            if row[f"{kind}_sku"]:
                components[row[f"{kind}_sku"]] = row[f"{kind}_name"]
    for setting in db.scalars(select(PortalSetting).where(PortalSetting.key.startswith(PREFIX, autoescape=True))):
        value = setting.value
        index[value["gp_sku"], value["gp_line_id"]] = {
            item["sku"]: item["name"] for item in value["items"] if item.get("enabled", True)
        }
    return index


def components_for(index, sku, line_id):
    return index.get((sku, line_id), index.get((sku, None), {}))


def imported_parents(db, plan, order_id, task_ids=None):
    task = ProductionScheduleItem
    child = aliased(task)
    query = select(task).join(task.demand_item).where(
        task.plan_id == plan.id, DemandItem.order_id == order_id,
        task.schedule_kind == "production", task.source_kind != "pf", task.excluded.is_(False),
        task.line_id.is_not(None), task.production_date.is_not(None), task.quantity_kg > 0,
        task.execution_status == "not_started",
        ~select(child.id).where(child.parent_task_id == task.id).exists(),
    ).options(joinedload(task.product), joinedload(task.line))
    if task_ids is not None:
        query = query.where(task.id.in_(task_ids))
    return {row.id: row for row in db.scalars(query)}


def import_pf_offer(db, plan, order_id):
    index = component_index(db)
    candidates = []
    for row in imported_parents(db, plan, order_id).values():
        components = components_for(index, row.product.sku, row.line_id)
        if components:
            candidates.append(dict(task_id=row.id, sku=row.product.sku, name=row.product.name,
                                   line_name=row.line.name, production_date=row.production_date,
                                   shift=row.shift, quantity_kg=row.quantity_kg, components=components))
    candidates.sort(key=lambda row: (row["production_date"], row["sku"], row["task_id"]))
    return dict(plan_id=plan.id, version=plan.revision, order_id=order_id, candidates=candidates)


def preview_parent(db, parent, options=None):
    return preview_expansion(db, {**(options or {}), "product_id": parent.product_id,
                             "line_id": parent.line_id, "quantity": parent.quantity_kg,
                             "production_date": parent.production_date, "source_kind": parent.source_kind},
                             scheduled=True)


def remove_import_tasks(db, plan, parents):
    expanded = [row for row in parents if (row.pf_details or {}).get("import_expansion")]
    parent_ids = {row.id for row in expanded}
    children = [row for row in plan.schedule_items if row.parent_task_id in parent_ids]
    if any(row.execution_status not in (None, "not_started") for row in [*expanded, *children]):
        raise HTTPException(409, "Задание ГП или связанный ПФ уже переданы в работу. Проверьте заменяемый план.")
    for row in [*children, *parents]:
        plan.schedule_items.remove(row)
        db.delete(row)
    db.flush()
    return {row.line_id for row in children}
