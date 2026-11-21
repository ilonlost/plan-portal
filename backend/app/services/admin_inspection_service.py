"""Explicit, read-only projection of planning data for portal administrators."""
import re

from app.models import entities as e


def redact_details(value):
    if isinstance(value, dict):
        return {key: "[скрыто]" if re.search(r"password|passwd|secret|token|credential|authorization|cookie|database_url|connection_string|private_key", key, re.I)
                else redact_details(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_details(item) for item in value]
    return value


# Never reflect arbitrary tables/columns or execute user-provided SQL. Settings,
# credentials, file blobs and version snapshots are intentionally not projected.
TABLES = {
    "products": ("Артикулы", e.Product, "id sku name active catalog_status state unit unit_weight_kg units_per_box box_weight_kg advance_status fk_status category mono_group reference_source"),
    "line_capabilities": ("Артикулы и мощности линий", e.LineCapability, "id product_id line_id units_per_hour speed_unit batch_quantum_kg min_order_kg min_batch max_batch capacity_type restrictions"),
    "production_lines": ("Производственные линии", e.ProductionLine, "id code name workshop_code workshop_name status working_hours priority schedule_code production_day_start_hour csb_line_code comments"),
    "line_capacities": ("Календарь мощностей", e.LineCapacity, "id line_id capacity_date shift available_hours available manual_override note"),
    "production_plans": ("Планы", e.ProductionPlan, "id name status active revision horizon_start horizon_end created_at updated_at"),
    "production_schedule_items": ("Задания плана", e.ProductionScheduleItem, "id plan_id product_id line_id demand_item_id parent_task_id production_date marking_date quantity quantity_kg source_quantity source_unit required_hours shift sequence schedule_kind source_kind source status excluded locked execution_status reason warnings pf_details"),
    "demand_items": ("Строки загруженных планов", e.DemandItem, "id order_id product_id sku product_name source_row quantity source_quantity source_unit source_kind requested_date due_date marking_date valid validation_errors raw_data"),
    "imported_orders": ("Источники загрузки", e.ImportedOrder, "id source_name template_type status imported_at total_rows valid_rows invalid_rows scope_line_id scope_start scope_end"),
    "production_plan_versions": ("Версии плана", e.ProductionPlanVersion, "id plan_id version_number change_type comment changed_by_id created_at"),
    "audit_events": ("Журнал изменений", e.AuditEvent, "id created_at username action entity_type entity_id details"),
    "users": ("Пользователи и права", e.User, "id username display_name role active workshop_code line_name auth_provider last_login_at created_at section_permissions"),
}


def table_columns(name):
    _, model, fields = TABLES[name]
    return {field: getattr(model, field) for field in fields.split()}
