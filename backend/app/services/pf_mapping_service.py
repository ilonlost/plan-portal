"""Planner-owned PF mappings, with the shipped workbook as the initial defaults."""
from copy import deepcopy
from hashlib import sha256
import json

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.models.entities import AuditEvent, LineCapability, PortalSetting, Product, ProductionLine

PREFIX = "pf_mapping:"


def mapping_key(sku, line_id):
    return PREFIX + sha256(json.dumps([sku, line_id]).encode()).hexdigest()


def initial_mapping(sku, lines):
    from app.services.pf_service import source_data, rules_for, _target_line

    rules = rules_for(sku)
    speed = next((row for row in source_data()["speeds"] if row["gp_sku"] == sku), {})
    return {"gp_sku": sku, "gp_name": rules[0]["gp_name"] if rules else "", "gp_line_id": None,
            "gp_speed_kg_hour": speed.get("gp_speed"), "revision": 0, "origin": "source",
            "items": [{"sku": row["sku"], "name": row["name"], "kind": row["kind"],
                       "line_id": target.id if (target := _target_line(row, lines)) else None,
                       "speed_kg_hour": speed.get("pf_speed") if speed.get("pf_sku") == row["sku"] else None,
                       "lead_days": row["lead_days"], "note": row["note"] or "", "enabled": True,
                       "date_defaulted": row["date_defaulted"]} for row in rules]}


def effective_mapping(db, sku, line_id):
    keys = [mapping_key(sku, line_id), mapping_key(sku, None)]
    saved = {row.key: row.value for row in db.scalars(select(PortalSetting).where(PortalSetting.key.in_(keys)))}
    for key in keys:
        if key in saved:
            return deepcopy(saved[key])
    lines = list(db.scalars(select(ProductionLine).where(ProductionLine.status == "active")))
    return initial_mapping(sku, lines)


def mapping_revision(mapping):
    return sha256(json.dumps(mapping, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def mapping_catalog(db):
    from app.services.pf_service import source_data

    lines = list(db.scalars(select(ProductionLine).order_by(ProductionLine.workshop_code, ProductionLine.name)))
    active_lines = [line for line in lines if line.status == "active"]
    products = list(db.scalars(select(Product).options(joinedload(Product.capabilities))).unique())
    product_map = {product.sku: product for product in products}
    defaults = {row["gp_sku"] for row in source_data()["rows"]}
    mappings = {(sku, None): initial_mapping(sku, active_lines) for sku in sorted(defaults)}
    for row in db.scalars(select(PortalSetting).where(PortalSetting.key.startswith(PREFIX, autoescape=True))):
        value = deepcopy(row.value)
        value["origin"] = "saved"
        value["updated_by"] = row.updated_by
        mappings[(value["gp_sku"], value["gp_line_id"])] = value
    choices = {product.sku: {"sku": product.sku, "name": product.name, "product_id": product.id,
                           "active": product.active and product.catalog_status == "active",
                           "capabilities": [{"line_id": cap.line_id, "speed_kg_hour": cap.units_per_hour} for cap in product.capabilities]}
               for product in products}
    for row in source_data()["rows"]:
        for kind in ("gp", "pf", "filling"):
            if row[f"{kind}_sku"]:
                choices.setdefault(row[f"{kind}_sku"], {"sku": row[f"{kind}_sku"], "name": row[f"{kind}_name"],
                                   "product_id": None, "active": False, "capabilities": []})
    for mapping in mappings.values():
        gp = product_map.get(mapping["gp_sku"])
        if gp:
            mapping["gp_name"] = gp.name
        mapping["gp_product_id"] = gp.id if gp else None
        for item in mapping["items"]:
            child = product_map.get(item["sku"])
            choices.setdefault(item["sku"], {"sku": item["sku"], "name": item["name"], "product_id": None,
                                            "active": False, "capabilities": []})
            if child:
                item["name"] = child.name
            eligible = [cap for cap in child.capabilities if any(line.id == cap.line_id for line in active_lines)] if child else []
            if item["line_id"] is None and len(eligible) == 1:
                item["line_id"] = eligible[0].line_id
            cap = next((cap for cap in eligible if cap.line_id == item["line_id"]), None)
            item["catalog_speed_kg_hour"] = cap.units_per_hour if cap else None
    return {"mappings": sorted(mappings.values(), key=lambda row: (row["gp_sku"], row["gp_line_id"] or 0)),
            "products": sorted(choices.values(), key=lambda row: row["sku"]),
            "lines": [{"id": line.id, "name": line.name, "workshop_name": line.workshop_name, "status": line.status} for line in lines]}


def save_mapping(db, payload, username):
    from fastapi import HTTPException
    from app.services.pf_service import source_data

    key = mapping_key(payload.gp_sku, payload.gp_line_id)
    row = db.scalar(select(PortalSetting).where(PortalSetting.key == key).with_for_update())
    if (row.value["revision"] if row else 0) != payload.revision:
        raise HTTPException(409, "Связи изменены другим планером. Обновите список и сопоставьте изменения")
    gp = db.scalar(select(Product).where(Product.sku == payload.gp_sku))
    source = next((entry for entry in source_data()["rows"] if entry["gp_sku"] == payload.gp_sku), None)
    if not gp and not source:
        raise HTTPException(422, "Выберите ГП из справочника")
    if payload.gp_line_id:
        line = db.get(ProductionLine, payload.gp_line_id)
        cap = db.scalar(select(LineCapability).where(LineCapability.product_id == gp.id, LineCapability.line_id == payload.gp_line_id)) if gp else None
        if not line or line.status != "active" or not cap or cap.units_per_hour <= 0:
            raise HTTPException(422, "Для выбранной линии ГП нет действующей связи со скоростью")
    skus = [item.sku for item in payload.items]
    if len(skus) != len(set(skus)) or payload.gp_sku in skus:
        raise HTTPException(422, "ПФ не должен повторяться или совпадать с ГП")
    for item in payload.items:
        if item.line_id:
            line = db.get(ProductionLine, item.line_id)
            if not line or (item.enabled and line.status != "active"):
                raise HTTPException(422, f"{item.sku}: выберите действующую линию ПФ")
    value = payload.model_dump(mode="json")
    children = {child.sku: child for child in db.scalars(select(Product).where(Product.sku.in_(skus)))} if skus else {}
    for item in value["items"]:
        if item["sku"] in children:
            item["name"] = children[item["sku"]].name
    value.update(gp_name=gp.name if gp else source["gp_name"], revision=payload.revision + 1, origin="saved")
    before = deepcopy(row.value) if row else None
    if row:
        row.value, row.updated_by = value, username
    else:
        db.add(PortalSetting(key=key, value=value, updated_by=username))
    db.add(AuditEvent(username=username, action="pf_mapping_updated", entity_type="pf_mapping", entity_id=payload.gp_sku,
                      details={"before": before, "after": value}))
    db.flush()
    return value
