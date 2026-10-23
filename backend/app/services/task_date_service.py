"""One date policy for source imports, placement, cards and exported plans."""
from datetime import time, timedelta

from app.core.line_catalog import line_key
from app.services.production_cycle_service import production_cycle


def is_bulka(line):
    return bool(line and line.workshop_code == "PC" and line_key(line.name) == "булка")


def uses_sandwich_window(line):
    return bool(line and line.workshop_code == "KC" and line_key(line.name) in {"сэндвичи", "сендвичи"} and production_cycle(line)["enabled"])


def is_advance(product):
    return bool(product and (product.advance_status or "").strip().upper() == "АЗ")


def ohl_day_offset(line, product):
    if is_bulka(line):
        return 0
    if uses_sandwich_window(line):
        return 1
    return int(is_advance(product))


def ohl_production_date(marking_date, line, product):
    return marking_date - timedelta(days=ohl_day_offset(line, product))


def shipping_date(item):
    if item.schedule_kind != "production":
        return None
    if item.shipping_date_override:
        return item.shipping_date_override
    if item.source_kind == "zam" or is_bulka(item.line):
        return item.production_date or item.marking_date
    marking = item.marking_date or (item.demand_item.source_plan_date if item.demand_item else None)
    if not marking:
        return None
    # Unclassified legacy articles retain their established day-before dispatch.
    scheduled = item.product and (item.product.advance_status or "").strip().casefold() == "по графику"
    offset = 1 if uses_sandwich_window(item.line) or not scheduled else 0
    return marking - timedelta(days=offset)


def shipping_time(item):
    if item.schedule_kind != "production":
        return None
    return item.shipping_time_override or (time(19) if uses_sandwich_window(item.line) else None)
