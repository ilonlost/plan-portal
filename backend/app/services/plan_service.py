from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.models.entities import (
    AuditEvent, DemandItem, ImportedOrder, LineCapability, LineCapacity, PlanStatus, PortalSetting, Product, ProductionLine, ProductionPlan,
    ProductionPlanVersion, ProductionScheduleItem, ScheduleStatus,
)
from app.services.planning_engine import CapabilityInput, CapacityInput, DemandInput, PlanningEngine
from app.services.planning_rules import mono_group


def cleaning_suppression_key(
    line_id: int,
    production_date: date,
    previous_group: str | None,
    previous_shift: str | None,
    following_group: str | None = None,
    following_shift: str | None = None,
) -> str:
    """Identify a wash boundary without relying on transient schedule item IDs."""
    payload = [
        line_id,
        production_date.isoformat(),
        previous_shift or "",
        previous_group or "",
        following_shift or "",
        following_group or "",
    ]
    return f"suppress_cleaning:{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}"


class PlanService:
    MIN_PRODUCTION_DURATION = Decimal("0.02")
    KG_ROUNDING_SETTING_KEY = "kg_rounding_up_v1"
    PIECE_AND_BOX_ROUNDING_SETTING_KEY = "piece_and_box_rounding_v3"
    LINE_PLANNING_RULES_SETTING_KEY = "line_planning_rules_v1"
    UNIVERSAL_OHL_PRIORITY_SETTING_KEY = "universal_ohl_priority_v1"
    def __init__(self, db: Session):
        self.db = db

    def active_plan(self) -> ProductionPlan | None:
        return self.db.scalar(
            select(ProductionPlan).where(ProductionPlan.active.is_(True)).order_by(ProductionPlan.updated_at.desc())
        )

    def correct_legacy_ohl_source_units(self) -> int:
        """Repair OHL imports made when the kg source was treated as pieces.

        The original cell value was preserved in ``source_quantity``, so the
        correction is deterministic and can safely rebuild the active plan.
        """
        rows = list(self.db.scalars(
            select(DemandItem).where(DemandItem.source_kind == "ohl", DemandItem.source_unit == "шт")
            .options(joinedload(DemandItem.product))
        ))
        if not rows:
            return 0
        for item in rows:
            quantity_kg = Decimal(item.source_quantity if item.source_quantity is not None else item.quantity)
            item.quantity = quantity_kg
            item.quantity_kg = quantity_kg
            item.source_unit = "кг"
            box_weight = Decimal(item.product.box_weight_kg) if item.product and item.product.box_weight_kg else None
            item.box_count = (quantity_kg / box_weight).quantize(Decimal("0.001")) if box_weight and box_weight > 0 else None
            item.validation_errors = [warning for warning in (item.validation_errors or []) if "шт." not in warning and "округлено до" not in warning]

        plan = self.active_plan()
        demands = self._latest_source_demands()
        if plan and demands:
            plan.horizon_start = min(item.requested_date for item in demands)
            plan.horizon_end = max(item.due_date for item in demands)
            self.calculate(plan, demands, "ohl_source_kg_correction")
            self.db.add(AuditEvent(
                username="system", action="ohl_source_kg_corrected", entity_type="production_plan", entity_id=str(plan.id),
                details={"demand_items": len(rows), "message": "ОХЛ: исходные значения исправлены с штук на кг"},
            ))
        self.db.commit()
        return len(rows)

    def apply_kg_rounding_upgrade(self) -> bool:
        """Rebuild the active plan once after enabling ceiling-to-kg quantities."""
        if self.db.scalar(select(PortalSetting).where(PortalSetting.key == self.KG_ROUNDING_SETTING_KEY)):
            return False
        plan = self.active_plan()
        demands = self._latest_source_demands()
        if not plan or not demands:
            return False
        plan.horizon_start = min(item.requested_date for item in demands)
        plan.horizon_end = max(item.due_date for item in demands)
        self.calculate(plan, demands, "kg_rounding_up")
        self.db.add(PortalSetting(key=self.KG_ROUNDING_SETTING_KEY, value={"rule": "ceil_to_whole_kg"}, updated_by="system"))
        self.db.add(AuditEvent(
            username="system", action="kg_rounding_up_applied", entity_type="production_plan", entity_id=str(plan.id),
            details={"message": "Задания в кг округлены вверх до целого значения"},
        ))
        self.db.commit()
        return True

    def apply_piece_and_box_rounding_upgrade(self) -> bool:
        """Rebuild the active plan once with whole pieces and full boxes.

        The demand sources are kg, but a released production task cannot contain
        a fraction of a piece or a fraction of a box.  Recalculation applies the
        product packaging quantum to every automatic split.  Manual tasks are
        upgraded in place as they do not take part in automatic recalculation.
        """
        if self.db.scalar(select(PortalSetting).where(PortalSetting.key == self.PIECE_AND_BOX_ROUNDING_SETTING_KEY)):
            return False
        plan = self.active_plan()
        demands = self._latest_source_demands()
        if not plan or not demands:
            return False
        plan.horizon_start = min(item.requested_date for item in demands)
        plan.horizon_end = max(item.due_date for item in demands)
        self.calculate(plan, demands, "piece_and_box_rounding")

        changed_manual_items = False
        for item in plan.schedule_items:
            if item.schedule_kind != "production" or item.source != "manual" or not item.product:
                continue
            rounded_quantity = self._round_quantity_for_product(Decimal(item.quantity_kg or item.quantity or 0), item.product)
            if rounded_quantity != Decimal(item.quantity or 0):
                item.quantity = rounded_quantity
                item.quantity_kg = rounded_quantity
                changed_manual_items = True
            item.box_count = self._box_count(rounded_quantity, item.product)
            if item.line_id and item.product_id:
                capability = self.db.scalar(select(LineCapability).where(
                    LineCapability.line_id == item.line_id, LineCapability.product_id == item.product_id,
                ))
                if capability and Decimal(capability.units_per_hour) > 0:
                    item.required_hours = self._production_duration(rounded_quantity, Decimal(capability.units_per_hour))
                    item.duration_hours = item.required_hours
        if changed_manual_items:
            self.refresh_sequence_and_cleanings(plan)
            self.recalculate_load(plan)
            self.create_version(plan, "manual_package_rounding", "Ручные задания округлены до целых коробов")

        self.db.add(PortalSetting(
            key=self.PIECE_AND_BOX_ROUNDING_SETTING_KEY,
            value={"rule": "ceil_pieces_and_full_boxes"},
            updated_by="system",
        ))
        self.db.add(AuditEvent(
            username="system", action="piece_and_box_rounding_applied", entity_type="production_plan", entity_id=str(plan.id),
            details={"message": "Штуки округлены вверх, задания приведены к полному коробу"},
        ))
        self.db.commit()
        return True

    def apply_line_planning_rules_upgrade(self) -> bool:
        """Apply newly introduced line-specific setup intervals to the active plan once."""
        if self.db.scalar(select(PortalSetting).where(PortalSetting.key == self.LINE_PLANNING_RULES_SETTING_KEY)):
            return False
        plan = self.active_plan()
        if plan:
            demands = self._latest_source_demands()
            if demands:
                self.calculate(plan, demands, "line_planning_rules_enabled")
            else:
                self.refresh_sequence_and_cleanings(plan)
                self.recalculate_load(plan)
                self.create_version(plan, "line_planning_rules_enabled", "Применены правила планирования по линиям")
        self.db.add(PortalSetting(
            key=self.LINE_PLANNING_RULES_SETTING_KEY,
            value={"lasagna_startup_hours": 1, "lasagna_changeover_hours": 1, "kc_restart_hours": 0.5},
            updated_by="system",
        ))
        self.db.add(AuditEvent(
            username="system", action="line_planning_rules_enabled", entity_type="production_plan",
            entity_id=str(plan.id) if plan else None,
            details={"message": "Применены интервалы запуска, переходов и запуска после простоя"},
        ))
        self.db.commit()
        return plan is not None

    def apply_universal_ohl_priority_upgrade(self) -> bool:
        """Rebuild the existing active plan once with OHL before ZAM on every line."""
        if self.db.scalar(select(PortalSetting).where(PortalSetting.key == self.UNIVERSAL_OHL_PRIORITY_SETTING_KEY)):
            return False
        plan = self.active_plan()
        if plan:
            demands = self._latest_source_demands()
            if demands:
                self.calculate(plan, demands, "universal_ohl_priority_enabled")
            else:
                self.refresh_sequence_and_cleanings(plan)
                self.recalculate_load(plan)
                self.create_version(plan, "universal_ohl_priority_enabled", "ОХЛ размещён перед ЗАМ на всех линиях")
        self.db.add(PortalSetting(
            key=self.UNIVERSAL_OHL_PRIORITY_SETTING_KEY,
            value={"source_order": ["ohl", "zam", "generic"]},
            updated_by="system",
        ))
        self.db.add(AuditEvent(
            username="system", action="universal_ohl_priority_enabled", entity_type="production_plan",
            entity_id=str(plan.id) if plan else None,
            details={"message": "На всех линиях задания ОХЛ размещены перед ЗАМ"},
        ))
        self.db.commit()
        return plan is not None

    def apply_complete_ohl_upgrade(self) -> bool:
        key = "complete_ohl_placement_v1"
        if self.db.scalar(select(PortalSetting).where(PortalSetting.key == key)):
            return False
        plan = self.active_plan()
        if plan:
            demands = self._latest_source_demands()
            if demands:
                self.calculate(plan, demands, "complete_ohl_placement")
            for item in plan.schedule_items:
                if item.source_kind == "zam":
                    item.marking_date = item.production_date
        self.db.add(PortalSetting(key=key, value={"enabled": True}, updated_by="system"))
        self.db.commit()
        return plan is not None

    def _latest_source_demands(self) -> list[DemandItem]:
        orders = list(self.db.scalars(select(ImportedOrder).where(
            ImportedOrder.template_type.in_(("ohl_daily", "quarter_weekly")),
        ).order_by(ImportedOrder.imported_at.desc(), ImportedOrder.id.desc())))
        latest_ids: list[int] = []
        seen: set[tuple[str, int | None]] = set()
        complete_seen: set[str] = set()
        for order in orders:
            if order.template_type in complete_seen:
                continue
            key = (order.template_type, order.scope_line_id)
            if key not in seen:
                # A removed latest file disables that source; do not resurrect an older upload.
                if order.status != "deleted":
                    latest_ids.append(order.id)
                seen.add(key)
            if order.scope_line_id is None:
                complete_seen.add(order.template_type)
        if not latest_ids:
            return []
        return [row for row in self.db.scalars(select(DemandItem).where(DemandItem.order_id.in_(latest_ids)).options(joinedload(DemandItem.product)))
                if not (row.raw_data or {}).get("replaced_by_line_id")]

    def recalculate_after_catalog_change(self, change_type: str = "catalog_updated") -> ProductionPlan | None:
        """Immediately rebuild the active plan after any catalog mutation."""
        self.db.flush()
        # Serialize catalog-triggered rebuilds and explicit user refreshes so
        # they cannot replace each other's generated schedule items.
        plan = self.db.scalar(
            select(ProductionPlan).where(ProductionPlan.active.is_(True))
            .order_by(ProductionPlan.updated_at.desc()).with_for_update()
        )
        if not plan:
            return None
        demands = self._latest_source_demands()
        if demands:
            return self.calculate(plan, demands, change_type)
        self.refresh_sequence_and_cleanings(plan)
        self.recalculate_load(plan)
        self.create_version(plan, change_type, "Справочник изменён; активный план обновлён")
        self.db.commit()
        return plan

    def calculate(self, plan: ProductionPlan, demands: list[DemandItem], change_type: str = "automatic_calculation", line_id: int | None = None, source_kind: str | None = None) -> ProductionPlan:
        if source_kind is not None:
            if line_id is None:
                raise ValueError("Source-specific recalculation requires a line")
            demands = [demand for demand in demands if demand.source_kind == source_kind]
        previous_requested_dates = {demand.id: demand.requested_date for demand in demands}
        auto_execution = {
            (row.demand_item_id, row.line_id, row.production_date, row.shift, Decimal(row.quantity)): {
                name: getattr(row, name) for name in ("execution_status", "execution_note", "reported_by", "reported_at", "actual_quantity_kg")
            }
            for row in plan.schedule_items if row.source == "auto" and row.schedule_kind == "production"
        }
        auto_sort_ranks = {
            (item.demand_item_id, item.line_id, item.production_date, item.shift): item.sort_rank
            for item in plan.schedule_items
            if item.source == "auto" and item.schedule_kind == "production" and item.sort_rank is not None
        }
        for demand in demands:
            if demand.source_kind == "ohl" and demand.source_plan_date and demand.product:
                demand.advance_production = demand.product.advance_status == "АЗ"
                override_date = (demand.raw_data or {}).get("az_override_production_date")
                demand.requested_date = date.fromisoformat(override_date) if override_date else demand.source_plan_date - timedelta(days=int(demand.advance_production))
                demand.due_date = demand.requested_date
                demand.production_week = demand.requested_date.isocalendar().week
        if demands:
            persistent_event_dates = [
                item.production_date for item in plan.schedule_items
                if item.production_date and not item.excluded and item.source != "auto" and item.schedule_kind != "production"
            ]
            plan.horizon_start = min([*(d.requested_date for d in demands), *persistent_event_dates, *([plan.horizon_start] if line_id else [])])
            plan.horizon_end = max([*(d.due_date for d in demands), *persistent_event_dates, *([plan.horizon_end] if line_id else [])])
            from app.services.line_schedule_service import ensure_line_capacities
            active_lines = list(self.db.scalars(select(ProductionLine).where(ProductionLine.status == "active")))
            ensure_line_capacities(self.db, active_lines, plan.horizon_start, plan.horizon_end)
        capabilities = list(self.db.scalars(
            select(LineCapability).join(LineCapability.line).join(LineCapability.product).where(
                ProductionLine.status == "active", Product.active.is_(True), Product.catalog_status == "active",
            ).options(joinedload(LineCapability.line))
        ))
        capabilities_by_key = {(capability.line_id, capability.product_id): capability for capability in capabilities}
        if line_id is not None:
            capabilities = [cap for cap in capabilities if cap.line_id == line_id]
        capabilities_by_product: dict[int, list[LineCapability]] = defaultdict(list)
        for capability in capabilities:
            capabilities_by_product[capability.product_id].append(capability)
        demand_by_id = {demand.id: demand for demand in demands}
        # Manually moved cards stay in place during normal recalculation, but
        # catalog changes still have to update dates, valid line mappings and
        # production quantities for those cards.
        for item in plan.schedule_items:
            if line_id is not None and item.line_id != line_id:
                continue
            if source_kind is not None and item.source_kind != source_kind:
                continue
            if item.source != "manual" or item.excluded or item.schedule_kind != "production" or not item.product_id:
                continue
            demand = demand_by_id.get(item.demand_item_id)
            if demand and demand.source_kind == "ohl":
                previous_date = previous_requested_dates.get(demand.id)
                if previous_date and previous_date != demand.requested_date and item.production_date:
                    # Keep a planner's relative offset from the requested date,
                    # while applying the catalog-driven date shift itself.
                    item.production_date += demand.requested_date - previous_date
            capability = capabilities_by_key.get((item.line_id, item.product_id))
            if capability is None:
                options = sorted(capabilities_by_product.get(item.product_id, []), key=lambda row: (row.line.priority, row.line_id))
                if options:
                    preferred_line = (demand.raw_data or {}).get("preferred_line_id") if demand else None
                    capability = next((row for row in options if row.line_id == preferred_line), options[0])
                    item.line_id = capability.line_id
            if capability:
                self._sync_manual_item_quantity(item, capability)
        mapped_product_ids = {capability.product_id for capability in capabilities}
        missing_capability_warning = "Артикул отсутствует в справочнике мощностей: он будет добавлен в список нераспределённых"
        for demand in demands:
            # This warning is a snapshot from import time. Once the catalog has
            # a usable line mapping, keep it from poisoning every subsequent
            # plan calculation.
            if demand.product_id in mapped_product_ids and demand.validation_errors:
                demand.validation_errors = [
                    warning for warning in demand.validation_errors
                    if warning != missing_capability_warning
                ]
        capacities = list(self.db.scalars(select(LineCapacity).where(
            LineCapacity.capacity_date >= plan.horizon_start,
            LineCapacity.capacity_date <= plan.horizon_end,
        )))
        # Preserve manually handled splits, including deleted quantities, across reimports.
        handled = defaultdict(lambda: Decimal("0"))
        for item in plan.schedule_items:
            if item.source == "manual" and item.demand_item and (line_id is None or item.line_id == line_id):
                consumed = item.source_quantity if item.source_unit == "кг" and item.source_quantity is not None else item.quantity
                handled[self.demand_key(item.demand_item)] += Decimal(consumed)
        remaining_quantity = {}
        for demand in demands:
            key = self.demand_key(demand)
            consumed = min(Decimal(demand.quantity), handled[key])
            handled[key] -= consumed
            remaining_quantity[demand.id] = Decimal(demand.quantity) - consumed
        reserved = defaultdict(lambda: Decimal("0"))
        reserved_groups = defaultdict(set)
        manual_cleaning_days = {(row.line_id, row.production_date) for row in plan.schedule_items if row.excluded and row.reason == "manual_cleaning_day"}
        capability_map = {(c.line_id, c.product_id): c for c in capabilities}
        for item in plan.schedule_items:
            if line_id is not None and item.line_id != line_id:
                continue
            frozen_production = source_kind is not None and item.schedule_kind == "production" and item.source_kind != source_kind
            if (item.source != "auto" or frozen_production) and not item.excluded and item.line_id and item.production_date:
                capability = capability_map.get((item.line_id, item.product_id))
                if item.schedule_kind == "production" and capability:
                    if not frozen_production:
                        item.required_hours = self._production_duration(Decimal(item.quantity), Decimal(capability.units_per_hour))
                    if capability.line.workshop_code == "PC":
                        reserved_groups[(item.line_id, item.production_date, item.shift)].add(mono_group(item.product.name, item.product.mono_group))
                reserved[(item.line_id, item.production_date, item.shift)] += Decimal(item.required_hours)
                if item.schedule_kind == "downtime":
                    line = item.line or self.db.get(ProductionLine, item.line_id)
                    default_restart = Decimal("0.5") if line and line.workshop_code == "KC" else Decimal("0")
                    reserved[(item.line_id, item.production_date, item.shift)] += Decimal(str((line.planning_settings or {}).get("restart_after_downtime_hours", default_restart))) if line else Decimal("0")
        for key, groups in reserved_groups.items():
            if (key[0], key[1]) not in manual_cleaning_days:
                reserved[key] += Decimal(len(groups))
        result = PlanningEngine().plan(
            demands=[DemandInput(
                id=item.id, product_id=item.product_id or 0, sku=item.sku, quantity=remaining_quantity[item.id],
                requested_date=item.requested_date, due_date=item.due_date, priority=item.priority,
                source_quantity=Decimal(item.source_quantity) if item.source_quantity is not None else None,
                source_unit=item.source_unit,
                unit_weight_kg=Decimal(item.product.unit_weight_kg) if item.product and item.product.unit_weight_kg else None,
                units_per_box=Decimal(item.product.units_per_box) if item.product and item.product.units_per_box else None,
                box_quantum_kg=Decimal(item.product.box_weight_kg) if item.product and item.product.box_weight_kg else None,
                exact_date=item.exact_date, source_kind=item.source_kind, marking_date=item.marking_date,
                warnings=tuple(item.validation_errors or []),
                mono_group=mono_group(item.product.name if item.product else item.product_name, item.product.mono_group if item.product else None),
                preferred_line_id=(item.raw_data or {}).get("preferred_line_id"),
            ) for item in demands if item.valid and remaining_quantity[item.id] > 0],
            capabilities=[CapabilityInput(
                line_id=item.line_id, product_id=item.product_id, units_per_hour=Decimal(item.units_per_hour),
                line_priority=item.line.priority,
                batch_quantum_kg=Decimal(item.batch_quantum_kg) if item.batch_quantum_kg else None,
                min_order_kg=Decimal(item.min_order_kg) if item.min_order_kg else None,
                workshop_code=item.line.workshop_code,
                daily_startup_hours=Decimal(str((item.line.planning_settings or {}).get("daily_startup_hours", 1 if "лазан" in item.line.name.casefold() else 0))),
                changeover_hours=Decimal(str((item.line.planning_settings or {}).get("changeover_hours", 1 if "лазан" in item.line.name.casefold() else 0))),
            ) for item in capabilities if item.line.status == "active"],
            capacities=[CapacityInput(
                line_id=item.line_id, capacity_date=item.capacity_date,
                available_hours=max(Decimal("0"), Decimal(item.available_hours) - reserved[(item.line_id, item.capacity_date, item.shift)]), available=item.available, shift=item.shift,
            ) for item in capacities],
            horizon_end=plan.horizon_end,
            reserved_groups=reserved_groups,
            manual_cleaning_days=manual_cleaning_days,
        )
        for existing in list(plan.schedule_items):
            replace_source = source_kind is None or existing.schedule_kind != "production" or existing.source_kind == source_kind
            if existing.source == "auto" and replace_source and (line_id is None or existing.line_id == line_id or existing.demand_item_id in demand_by_id):
                plan.schedule_items.remove(existing)
                self.db.delete(existing)
        self.db.flush()
        for item in result:
            plan.schedule_items.append(ProductionScheduleItem(
                demand_item_id=item.demand_id, product_id=item.product_id, line_id=item.line_id,
                production_date=item.production_date, sequence=0, quantity=item.quantity,
                source_quantity=item.source_quantity, source_unit=item.source_unit,
                quantity_kg=item.quantity_kg, box_count=item.box_count, batch_count=item.batch_count,
                source_kind=item.source_kind, marking_date=item.marking_date,
                shift=item.shift,
                sort_rank=auto_sort_ranks.get((item.demand_id, item.line_id, item.production_date, item.shift)),
                required_hours=item.required_hours, status=ScheduleStatus(item.status),
                source="auto", warnings=item.warnings,
                **auto_execution.get((item.demand_id, item.line_id, item.production_date, item.shift, Decimal(item.quantity)), {}),
            ))
        plan.status = PlanStatus.NEEDS_REVIEW if any(item.status in {"conflict", "unscheduled"} for item in result) else PlanStatus.CALCULATED
        self.db.flush()
        self.refresh_sequence_and_cleanings(plan, line_id=line_id, source_kind=source_kind)
        self.recalculate_load(plan, line_id=line_id, source_kind=source_kind)
        self.create_version(plan, change_type, "Автоматический расчёт по правилам конечной мощности")
        self.db.commit()
        self.db.refresh(plan)
        return plan

    def recalculate_load(self, plan: ProductionPlan, line_id: int | None = None, source_kind: str | None = None) -> None:
        scope_line_id = line_id
        lines = {line.id: line for line in self.db.scalars(select(ProductionLine))}
        dated_items = [item for item in plan.schedule_items if item.line_id and item.production_date and not item.excluded and (line_id is None or item.line_id == line_id)]
        capacity_rows = {
            (item.line_id, item.capacity_date, item.shift): Decimal(item.available_hours) if item.available else Decimal("0")
            for item in self.db.scalars(select(LineCapacity).where(
                LineCapacity.line_id.in_({item.line_id for item in dated_items}),
                LineCapacity.capacity_date >= min(item.production_date for item in dated_items),
                LineCapacity.capacity_date <= max(item.production_date for item in dated_items),
            ))
        } if dated_items else {}
        groups: dict[tuple[int, date, str], list[ProductionScheduleItem]] = defaultdict(list)
        for item in plan.schedule_items:
            if scope_line_id is not None and item.line_id != scope_line_id:
                continue
            if item.line_id and item.production_date and not item.excluded:
                groups[(item.line_id, item.production_date, item.shift)].append(item)
        for (line_id, production_date, shift), items in groups.items():
            capacity = capacity_rows.get((line_id, production_date, shift), Decimal(lines[line_id].working_hours) / Decimal("2"))
            total_hours = sum((Decimal(item.required_hours) for item in items), Decimal("0"))
            load = (total_hours / capacity * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) if capacity else Decimal("999")
            for item in items:
                if source_kind is not None and item.schedule_kind == "production" and item.source_kind != source_kind:
                    continue
                warnings = [warning for warning in (item.warnings or []) if not warning.startswith("Загрузка линии")]
                item.load_percent = load
                if load > 100:
                    item.status = ScheduleStatus.WARNING if item.source_kind == "ohl" and item.schedule_kind == "production" else ScheduleStatus.CONFLICT
                    warnings.append(f"Загрузка линии {load}% превышает доступную мощность")
                elif load >= 85 and item.status != ScheduleStatus.CONFLICT:
                    item.status = ScheduleStatus.WARNING
                    warnings.append(f"Загрузка линии близка к пределу: {load}%")
                elif item.status not in {ScheduleStatus.CONFLICT, ScheduleStatus.UNSCHEDULED}:
                    item.status = ScheduleStatus.PLANNED
                item.warnings = warnings
        for item in plan.schedule_items:
            if scope_line_id is not None and item.line_id != scope_line_id:
                continue
            if source_kind is not None and item.schedule_kind == "production" and item.source_kind != source_kind:
                continue
            if item.line_id is None or item.production_date is None:
                item.load_percent = 0
                item.status = ScheduleStatus.UNSCHEDULED
        plan.status = PlanStatus.NEEDS_REVIEW if any(not item.excluded and (item.status in {ScheduleStatus.CONFLICT, ScheduleStatus.UNSCHEDULED} or item.load_percent > 100) for item in plan.schedule_items) else PlanStatus.CALCULATED
        self.db.flush()

    def update_item(self, item: ProductionScheduleItem, values: dict) -> ProductionPlan:
        plan = item.plan
        if item.line_id and item.production_date and any(key in values for key in ("before_item_id", "sequence", "duration_minutes")):
            self._preserve_pc_day_order(plan, item.line_id, item.production_date)
        next_line = values.get("line_id") or item.line_id
        if values.get("shift") not in {None, "day", "night"}:
            raise ValueError("Выберите дневную или ночную смену")
        if item.schedule_kind == "production" and next_line and not self.db.scalar(select(LineCapability).where(LineCapability.line_id == next_line, LineCapability.product_id == item.product_id)):
            raise ValueError("В справочнике нет связи этого артикула с выбранной линией")
        for field in ("production_date", "line_id", "shift", "locked", "excluded"):
            if field in values and values[field] is not None:
                setattr(item, field, values[field])
        if "marking_date" in values:
            item.marking_date = values["marking_date"]
        if item.source_kind == "zam":
            item.marking_date = item.production_date
        if item.line_id and item.production_date and any(key in values for key in ("before_item_id", "sequence", "duration_minutes")):
            self._preserve_pc_day_order(plan, item.line_id, item.production_date)
        if values.get("duration_minutes") is not None:
            if item.schedule_kind == "production":
                raise ValueError("Время продукции рассчитывается по скорости линии")
            item.required_hours = Decimal(values["duration_minutes"]) / Decimal("60")
            item.duration_hours = item.required_hours
            item.start_time = item.end_time = None
        capability = None
        if item.schedule_kind == "production" and item.line_id and item.product_id:
            capability = self.db.scalar(select(LineCapability).where(
                LineCapability.line_id == item.line_id, LineCapability.product_id == item.product_id,
            ))
        if values.get("quantity") is not None:
            item.quantity = self._round_quantity_for_product(Decimal(values["quantity"]), item.product, capability)
            item.quantity_kg = item.quantity
            item.box_count = self._box_count(item.quantity, item.product)
            if capability and capability.batch_quantum_kg and Decimal(capability.batch_quantum_kg) > 0:
                item.batch_count = (item.quantity / Decimal(capability.batch_quantum_kg)).quantize(Decimal("0.001"))
        if item.schedule_kind == "production" and item.line_id and item.product_id:
            if capability and Decimal(capability.units_per_hour) > 0:
                item.required_hours = self._production_duration(Decimal(item.quantity), Decimal(capability.units_per_hour))
                item.duration_hours = item.required_hours
        if item.line_id and item.production_date:
            selected = self.db.scalar(select(LineCapacity).where(
                LineCapacity.line_id == item.line_id,
                LineCapacity.capacity_date == item.production_date,
                LineCapacity.shift == item.shift,
            ))
            if not selected or not selected.available or Decimal(selected.available_hours) <= 0:
                replacement = self.db.scalar(select(LineCapacity).where(
                    LineCapacity.line_id == item.line_id,
                    LineCapacity.capacity_date == item.production_date,
                    LineCapacity.available.is_(True),
                    LineCapacity.available_hours > 0,
                ).order_by(LineCapacity.shift))
                if replacement:
                    item.shift = replacement.shift
        item.source = "manual"
        if ("before_item_id" in values or values.get("sequence") is not None) and item.line_id and item.production_date:
            peers = [row for row in plan.schedule_items if not row.excluded and (row.schedule_kind == "production" or row.source != "auto") and row.line_id == item.line_id and row.production_date == item.production_date]
            peers.sort(key=lambda row: (Decimal(row.sort_rank) if row.sort_rank is not None else Decimal(row.sequence or 0) * 10, row.id or 0))
            peers = [row for row in peers if row is not item]
            before_id = values.get("before_item_id")
            index = min(values["sequence"] - 1, len(peers)) if values.get("sequence") is not None else next((idx for idx, row in enumerate(peers) if row.id == before_id), len(peers))
            peers.insert(index, item)
            for rank, row in enumerate(peers, start=1):
                row.sort_rank = Decimal(rank * 10)
        self.refresh_sequence_and_cleanings(plan)
        self.recalculate_load(plan)
        self.create_version(plan, "manual_change", values.get("comment") or "Ручная корректировка задания")
        self.db.commit()
        self.db.refresh(item)
        return plan

    @classmethod
    def _production_duration(cls, quantity: Decimal, speed: Decimal) -> Decimal:
        if quantity <= 0 or speed <= 0:
            return Decimal("0")
        return max(cls.MIN_PRODUCTION_DURATION, (quantity / speed).quantize(Decimal("0.01"), rounding=ROUND_CEILING))

    @staticmethod
    def _ceil_kg(quantity: Decimal) -> Decimal:
        return quantity.to_integral_value(rounding=ROUND_CEILING)

    @staticmethod
    def _ceil_quantum(quantity: Decimal, quantum: Decimal) -> Decimal:
        if quantum <= 0:
            return quantity
        return (quantity / quantum).to_integral_value(rounding=ROUND_CEILING) * quantum

    @classmethod
    def _round_quantity_for_product(cls, quantity: Decimal, product: Product | None, capability: LineCapability | None = None) -> Decimal:
        rounded_kg = quantity
        box_weight = Decimal(product.box_weight_kg) if product and product.box_weight_kg else None
        if box_weight and box_weight > 0:
            return cls._ceil_quantum(rounded_kg, box_weight)
        unit_weight = Decimal(product.unit_weight_kg) if product and product.unit_weight_kg else None
        if unit_weight and unit_weight > 0:
            return cls._ceil_quantum(rounded_kg, unit_weight)
        batch_quantum = Decimal(capability.batch_quantum_kg) if capability and capability.batch_quantum_kg else None
        return cls._ceil_quantum(rounded_kg, batch_quantum) if batch_quantum and batch_quantum > 0 else rounded_kg

    @classmethod
    def _sync_manual_item_quantity(cls, item: ProductionScheduleItem, capability: LineCapability) -> None:
        quantity = cls._round_quantity_for_product(Decimal(item.quantity_kg or item.quantity or 0), item.product, capability)
        item.quantity = quantity
        item.quantity_kg = quantity
        item.box_count = cls._box_count(quantity, item.product)
        quantum = Decimal(capability.batch_quantum_kg) if capability.batch_quantum_kg else None
        item.batch_count = (quantity / quantum).quantize(Decimal("0.001")) if quantum and quantum > 0 else None
        item.required_hours = cls._production_duration(quantity, Decimal(capability.units_per_hour))
        item.duration_hours = item.required_hours

    @staticmethod
    def _box_count(quantity: Decimal, product: Product | None) -> Decimal | None:
        box_weight = Decimal(product.box_weight_kg) if product and product.box_weight_kg else None
        if not box_weight or box_weight <= 0:
            return None
        return (quantity / box_weight).to_integral_value(rounding=ROUND_CEILING)

    def create_manual_task(self, plan: ProductionPlan, values: dict) -> ProductionPlan:
        if values.get("source_kind", "generic") not in {"ohl", "zam", "generic", "pf"}:
            raise ValueError("Выберите источник: ОХЛ, ЗАМ, Прочее или ПФ")
        product = self.db.get(Product, values["product_id"])
        line = self.db.get(ProductionLine, values["line_id"])
        if not product or not line:
            raise ValueError("Выберите существующие SKU и линию")
        capability = self.db.scalar(select(LineCapability).where(
            LineCapability.line_id == line.id, LineCapability.product_id == product.id,
        ))
        if not capability or Decimal(capability.units_per_hour) <= 0:
            raise ValueError("Для выбранного SKU не задана скорость на этой линии")
        quantity = self._round_quantity_for_product(Decimal(values["quantity"]), product, capability)
        if quantity <= 0:
            raise ValueError("Задание должно быть больше нуля")
        quantum = Decimal(capability.batch_quantum_kg) if capability.batch_quantum_kg else None
        boxes = self._box_count(quantity, product)
        batches = (quantity / quantum).quantize(Decimal("0.001")) if quantum and quantum > 0 else None
        required_hours = self._production_duration(quantity, Decimal(capability.units_per_hour))
        item = ProductionScheduleItem(
            plan_id=plan.id, product_id=product.id, line_id=line.id,
            production_date=values["production_date"], marking_date=values["production_date"] if values.get("source_kind") == "zam" else values.get("marking_date") or values["production_date"],
            shift=values.get("shift", "day"), sequence=len(plan.schedule_items) + 1,
            quantity=quantity, source_quantity=quantity, source_unit="кг", quantity_kg=quantity,
            box_count=boxes, batch_count=batches, required_hours=required_hours, duration_hours=required_hours,
            schedule_kind="production", source_kind=values.get("source_kind") or "generic", source="manual", locked=True,
            status=ScheduleStatus.PLANNED, warnings=[],
        )
        plan.schedule_items.append(item)
        self.db.flush()
        self.refresh_sequence_and_cleanings(plan)
        self.recalculate_load(plan)
        self.create_version(plan, "manual_task_created", f"Создано вручную: {product.sku} · {quantity} кг")
        self.db.commit()
        return plan

    def create_event(self, plan: ProductionPlan, values: dict) -> ProductionPlan:
        kind = values["schedule_kind"]
        if kind not in {"cleaning", "downtime", "maintenance", "trial", "gas", "startup", "changeover"}:
            raise ValueError("Неизвестный тип события линии")
        start_time = values.get("start_time")
        end_time = values.get("end_time")
        if (start_time is None) != (end_time is None):
            raise ValueError("Укажите оба времени или только длительность")
        if start_time is not None and end_time is not None:
            start_at = datetime.combine(values["production_date"], start_time)
            end_at = datetime.combine(values["production_date"], end_time)
            if end_at <= start_at:
                end_at += timedelta(days=1)
            duration_hours = Decimal(str((end_at - start_at).total_seconds() / 3600)).quantize(Decimal("0.01"))
        else:
            duration_hours = Decimal(str(values.get("duration_hours") or 0))
        if duration_hours <= 0 or duration_hours > 24:
            raise ValueError("Интервал события должен быть больше 0 и не больше 24 часов")
        event = ProductionScheduleItem(
            plan_id=plan.id, product_id=None, line_id=values["line_id"],
            production_date=values["production_date"], shift=values.get("shift", "day"),
            sequence=len(plan.schedule_items) + 1, quantity=Decimal("0"), quantity_kg=Decimal("0"),
            required_hours=duration_hours, duration_hours=duration_hours, start_time=start_time, end_time=end_time,
            schedule_kind=kind, reason=values["reason"], source="manual", locked=True,
            status=ScheduleStatus.PLANNED,
        )
        plan.schedule_items.append(event)
        self.db.flush()
        self.refresh_sequence_and_cleanings(plan)
        self.recalculate_load(plan)
        self.create_version(plan, f"manual_{kind}", f"{kind}: {values['reason']}")
        self.db.commit()
        return plan

    def delete_item(self, item: ProductionScheduleItem) -> ProductionPlan:
        plan = item.plan
        description = item.reason or (item.product.name if item.product else "задание")
        if item.schedule_kind == "cleaning" and item.source == "auto" and item.line_id and item.production_date:
            siblings = sorted(
                [row for row in plan.schedule_items if not row.excluded and row.line_id == item.line_id and row.production_date == item.production_date],
                key=lambda row: (row.sequence, row.id or 0),
            )
            position = siblings.index(item)
            previous = next((row for row in reversed(siblings[:position]) if row.schedule_kind == "production"), None)
            following = next((row for row in siblings[position + 1:] if row.schedule_kind == "production"), None)
            previous_group = mono_group(previous.product.name, previous.product.mono_group) if previous and previous.product else None
            following_group = mono_group(following.product.name, following.product.mono_group) if following and following.product else None
            item.reason = cleaning_suppression_key(
                item.line_id,
                item.production_date,
                previous_group,
                previous.shift if previous else item.shift,
                following_group,
                following.shift if following else None,
            )
        # Keep a tombstone tied to source identity, including after reimport.
        item.excluded = True
        item.source = "manual"
        item.locked = True
        self.db.flush()
        self.refresh_sequence_and_cleanings(plan)
        self.recalculate_load(plan)
        self.create_version(plan, "manual_delete", f"Удалено: {description}")
        self.db.commit()
        return plan

    @staticmethod
    def demand_key(item: DemandItem) -> tuple:
        return (item.source_kind, item.sku, item.source_plan_date or item.requested_date)

    def approve(self, plan: ProductionPlan, comment: str | None = None) -> ProductionPlan:
        if any(not item.excluded and item.status in {ScheduleStatus.CONFLICT, ScheduleStatus.UNSCHEDULED} for item in plan.schedule_items):
            raise ValueError("Нельзя утвердить план с конфликтами или нераспределёнными заданиями")
        plan.status = PlanStatus.APPROVED
        self.create_version(plan, "approval", comment or "План утверждён")
        self.db.commit()
        return plan

    def update_execution_status(self, item: ProductionScheduleItem, status: str, note: str | None, username: str) -> ProductionPlan:
        allowed = {"not_started", "exported", "in_progress", "completed", "partially_shipped", "not_shipped"}
        if status not in allowed:
            raise ValueError("Неизвестный статус исполнения")
        if status in {"not_shipped", "partially_shipped"} and not (note or "").strip():
            raise ValueError("Для неотгрузки или частичной отгрузки укажите причину")
        item.execution_status = status
        item.execution_note = (note or "").strip() or None
        item.reported_by = username
        item.reported_at = datetime.now(timezone.utc)
        self.create_version(item.plan, "execution_status", f"{item.product.name if item.product else 'Задание'}: {status}. {item.execution_note or ''}".strip())
        self.db.commit()
        return item.plan

    def _preserve_pc_day_order(self, plan: ProductionPlan, line_id: int, production_date: date) -> None:
        """A planner-managed PC day keeps its existing washes and explicit order."""
        line = self.db.get(ProductionLine, line_id)
        if not line or line.workshop_code != "PC":
            return
        if any(row.excluded and row.reason == "manual_cleaning_day" and row.line_id == line_id and row.production_date == production_date for row in plan.schedule_items):
            return
        rows = sorted([row for row in plan.schedule_items if not row.excluded and row.line_id == line_id and row.production_date == production_date], key=lambda row: (row.sequence, row.id or 0))
        for index, row in enumerate(rows, start=1):
            row.sort_rank = Decimal(index * 10)
            if row.schedule_kind == "cleaning" and row.source == "auto":
                row.source = "manual"
        plan.schedule_items.append(ProductionScheduleItem(
            line_id=line_id, production_date=production_date, schedule_kind="cleaning", source="manual",
            excluded=True, quantity=Decimal("0"), required_hours=Decimal("0"), reason="manual_cleaning_day",
        ))
        self.db.flush()

    def refresh_sequence_and_cleanings(self, plan: ProductionPlan, line_id: int | None = None, source_kind: str | None = None) -> None:
        # A per-source upload preserves the other source's cards, including their positions.
        anchors = {row.id: row.sequence for row in plan.schedule_items
                   if source_kind is not None and row.line_id == line_id and row.schedule_kind == "production" and row.source_kind != source_kind}
        for existing in list(plan.schedule_items):
            if line_id is not None and existing.line_id != line_id:
                continue
            if existing.source == "auto" and existing.schedule_kind in {"cleaning", "startup", "changeover", "restart"}:
                plan.schedule_items.remove(existing)
                self.db.delete(existing)
        self.db.flush()
        lines = {line.id: line for line in self.db.scalars(select(ProductionLine))}
        products = {product.id: product for product in self.db.scalars(select(Product))}
        grouped: dict[tuple[int, date], list[ProductionScheduleItem]] = defaultdict(list)
        unscheduled: list[ProductionScheduleItem] = []
        for item in plan.schedule_items:
            if line_id is not None and item.line_id != line_id:
                continue
            if item.line_id and item.production_date and not item.excluded:
                grouped[(item.line_id, item.production_date)].append(item)
            else:
                unscheduled.append(item)
        source_rank = {"ohl": 0, "zam": 1, "generic": 2}
        suppressed = {
            item.reason for item in plan.schedule_items
            if item.excluded and item.source == "manual" and item.schedule_kind == "cleaning" and (item.reason or "").startswith("suppress_cleaning:")
        }
        manual_days = {(row.line_id, row.production_date) for row in plan.schedule_items if row.excluded and row.reason == "manual_cleaning_day"}
        for (line_id, production_date), items in sorted(grouped.items(), key=lambda value: (value[0][1], lines[value[0][0]].priority)):
            line = lines[line_id]
            name = line.name.casefold()
            settings = {
                "daily_startup_hours": 1 if "лазан" in name else 0,
                "changeover_hours": 1 if "лазан" in name else 0,
                "restart_after_downtime_hours": Decimal("0.5") if line.workshop_code == "KC" else 0,
                "ohl_first": "лазан" in name,
                "bolognese_last": "лазан" in name,
                "start_batch_number": 4 if "лазан" in name else 1,
                **(line.planning_settings or {}),
            }
            productions = [item for item in items if item.schedule_kind == "production"]
            events = [item for item in items if item.schedule_kind != "production" and item.source != "auto"]
            productions.sort(key=lambda item: (
                source_rank.get(item.source_kind, 2),
                0 if item.shift == "day" else 1,
                Decimal(item.sort_rank) if item.sort_rank is not None else Decimal("999999"),
                1 if settings["bolognese_last"] and item.product_id in products and "болоньез" in products[item.product_id].name.casefold() else 0,
                mono_group(products[item.product_id].name, products[item.product_id].mono_group) if item.product_id in products else "",
                item.sequence, item.id or 0,
            ))
            sequence = 1
            previous_group: str | None = None
            previous_shift = "day"
            if productions and Decimal(str(settings["daily_startup_hours"])) > 0:
                hours = Decimal(str(settings["daily_startup_hours"]))
                plan.schedule_items.append(ProductionScheduleItem(
                    plan_id=plan.id, line_id=line_id, production_date=production_date, shift=productions[0].shift,
                    sequence=sequence, quantity=Decimal("0"), quantity_kg=Decimal("0"), required_hours=hours, duration_hours=hours,
                    schedule_kind="startup", reason=f"Запуск линии · старт с кванта {settings['start_batch_number']}",
                    source_kind="generic", source="auto", locked=True, status=ScheduleStatus.PLANNED,
                ))
                sequence += 1
            for item in productions:
                product = products.get(item.product_id)
                current_group = mono_group(product.name, product.mono_group) if product else str(item.product_id)
                transition = previous_group and (previous_group != current_group or previous_shift != item.shift)
                suppression_key = cleaning_suppression_key(
                    line_id, production_date, previous_group, previous_shift, current_group, item.shift,
                )
                if line.workshop_code == "PC" and (line_id, production_date) not in manual_days and transition and suppression_key not in suppressed:
                    wash = ProductionScheduleItem(
                        plan_id=plan.id, product_id=None, line_id=line_id, production_date=production_date,
                        shift=previous_shift, sequence=sequence, quantity=Decimal("0"), quantity_kg=Decimal("0"),
                        required_hours=Decimal("1"), duration_hours=Decimal("1"), schedule_kind="cleaning",
                        reason=None,
                        source_kind="generic", source="auto", locked=True, status=ScheduleStatus.PLANNED,
                    )
                    plan.schedule_items.append(wash)
                    sequence += 1
                elif line.workshop_code != "PC" and transition and Decimal(str(settings["changeover_hours"])) > 0:
                    hours = Decimal(str(settings["changeover_hours"]))
                    plan.schedule_items.append(ProductionScheduleItem(
                        plan_id=plan.id, line_id=line_id, production_date=production_date, shift=previous_shift,
                        sequence=sequence, quantity=Decimal("0"), quantity_kg=Decimal("0"), required_hours=hours, duration_hours=hours,
                        schedule_kind="changeover", reason="Переход на другой вид продукции", source_kind="generic", source="auto", locked=True, status=ScheduleStatus.PLANNED,
                    ))
                    sequence += 1
                item.sequence = sequence
                sequence += 1
                previous_group = current_group
                previous_shift = item.shift
            end_suppression = cleaning_suppression_key(
                line_id, production_date, previous_group, previous_shift,
            )
            if line.workshop_code == "PC" and (line_id, production_date) not in manual_days and previous_group and end_suppression not in suppressed:
                plan.schedule_items.append(ProductionScheduleItem(
                    plan_id=plan.id, product_id=None, line_id=line_id, production_date=production_date,
                    shift=previous_shift, sequence=sequence, quantity=Decimal("0"), quantity_kg=Decimal("0"),
                    required_hours=Decimal("1"), duration_hours=Decimal("1"), schedule_kind="cleaning",
                    reason=None,
                    source_kind="generic", source="auto", locked=True, status=ScheduleStatus.PLANNED,
                ))
                sequence += 1
            for event in sorted(events, key=lambda value: (0 if value.shift == "day" else 1, Decimal(value.sort_rank) if value.sort_rank is not None else Decimal("999999"), value.sequence, value.id or 0)):
                event.sequence = sequence
                sequence += 1
                if event.schedule_kind == "downtime" and Decimal(str(settings["restart_after_downtime_hours"])) > 0:
                    hours = Decimal(str(settings["restart_after_downtime_hours"]))
                    plan.schedule_items.append(ProductionScheduleItem(
                        plan_id=plan.id, line_id=line_id, production_date=production_date, shift=event.shift,
                        sequence=sequence, quantity=Decimal("0"), quantity_kg=Decimal("0"), required_hours=hours, duration_hours=hours,
                        schedule_kind="restart", reason="Запуск линии после простоя", source_kind="generic", source="auto", locked=True, status=ScheduleStatus.PLANNED,
                    ))
                    sequence += 1
            if (line_id, production_date) in manual_days:
                ordered = [row for row in plan.schedule_items if not row.excluded and row.line_id == line_id and row.production_date == production_date]
                ordered.sort(key=lambda row: (Decimal(row.sort_rank) if row.sort_rank is not None else Decimal(row.sequence or 0) * 10, row.sequence, row.id or 0))
                for index, row in enumerate(ordered, start=1):
                    row.sequence = index
        for sequence, item in enumerate(unscheduled, start=1):
            item.sequence = sequence
        if anchors:
            scoped_groups = defaultdict(list)
            for row in plan.schedule_items:
                if row.line_id == line_id:
                    scoped_groups[row.production_date].append(row)
            for rows in scoped_groups.values():
                reserved = {anchors[row.id] for row in rows if row.id in anchors}
                next_sequence = 1
                for row in sorted(rows, key=lambda value: (value.sequence, value.id or 0)):
                    if row.id in anchors:
                        row.sequence = anchors[row.id]
                        next_sequence = max(next_sequence, row.sequence + 1)
                    else:
                        while next_sequence in reserved:
                            next_sequence += 1
                        row.sequence = next_sequence
                        next_sequence += 1
        self.db.flush()

    def create_version(self, plan: ProductionPlan, change_type: str, comment: str) -> None:
        plan.updated_at = datetime.now(timezone.utc)
        self.db.flush()
        current = self.db.scalar(select(func.max(ProductionPlanVersion.version_number)).where(ProductionPlanVersion.plan_id == plan.id)) or 0
        snapshot = {"status": plan.status.value, "items": [
            {"id": item.id, "date": item.production_date.isoformat() if item.production_date else None, "line_id": item.line_id,
             "quantity": str(item.quantity), "status": item.status.value, "locked": item.locked}
            for item in plan.schedule_items
        ]}
        self.db.add(ProductionPlanVersion(plan_id=plan.id, version_number=current + 1, change_type=change_type, comment=comment, snapshot=snapshot))


def schedule_item_dict(item: ProductionScheduleItem) -> dict:
    quantity_units = None
    if item.source_unit == "шт" and item.source_quantity is not None:
        quantity_units = Decimal(item.source_quantity).to_integral_value(rounding=ROUND_CEILING)
    elif item.product:
        unit_weight = Decimal(item.product.unit_weight_kg or 0)
        if unit_weight <= 0 and item.product.box_weight_kg and item.product.units_per_box:
            unit_weight = Decimal(item.product.box_weight_kg) / Decimal(item.product.units_per_box)
        if unit_weight > 0:
            quantity_units = (Decimal(item.quantity_kg or item.quantity or 0) / unit_weight).to_integral_value(rounding=ROUND_CEILING)
    return {
        "id": item.id,
        "sequence": item.sequence,
        "sort_rank": item.sort_rank,
        "production_date": item.production_date,
        "line_id": item.line_id,
        "line_code": item.line.code if item.line else None,
        "line_name": item.line.name if item.line else None,
        "workshop_code": item.line.workshop_code if item.line else None,
        "workshop_name": item.line.workshop_name if item.line else None,
        "product_id": item.product_id,
        "product_name": item.product.name if item.product else ({"cleaning": "Мойка линии", "downtime": "Простой", "maintenance": "ТО", "trial": "Проработка", "startup": "Запуск линии", "changeover": "Переход", "restart": "Запуск после простоя"}.get(item.schedule_kind, "Служебное событие")),
        "sku": item.product.sku if item.product else "—",
        "quantity": item.quantity,
        "source_quantity": item.source_quantity,
        "source_unit": item.source_unit,
        "quantity_kg": item.quantity_kg,
        "quantity_units": quantity_units,
        "mono_group": mono_group(item.product.name, item.product.mono_group) if item.product else None,
        "box_count": item.box_count,
        "batch_count": item.batch_count,
        "schedule_kind": item.schedule_kind,
        "duration_hours": item.duration_hours,
        "start_time": item.start_time,
        "end_time": item.end_time,
        "reason": item.reason,
        "actual_quantity_kg": item.actual_quantity_kg,
        "source_kind": item.source_kind,
        "marking_date": item.production_date if item.source_kind == "zam" else item.marking_date,
        "shipping_date": item.production_date if item.source_kind == "zam" else (
            item.marking_date
            or (item.demand_item.due_date if item.demand_item and item.demand_item.source_kind == "ohl" else None)
        ) - timedelta(days=1) if (
            item.marking_date
            or (item.demand_item.due_date if item.demand_item and item.demand_item.source_kind == "ohl" else None)
        ) else None,
        "execution_status": item.execution_status,
        "execution_note": item.execution_note,
        "reported_by": item.reported_by,
        "reported_at": item.reported_at,
        "shift": item.shift,
        "required_hours": item.required_hours,
        "load_percent": item.load_percent,
        "status": item.status.value,
        "source": item.source,
        "locked": item.locked,
        "excluded": item.excluded,
        "due_date": item.demand_item.due_date if item.demand_item else None,
        "warnings": item.warnings or [],
    }


def plan_dict(db: Session, plan: ProductionPlan, allowed_line_name: str | None = None) -> dict:
    items = list(db.scalars(
        select(ProductionScheduleItem)
        .where(ProductionScheduleItem.plan_id == plan.id, ProductionScheduleItem.excluded.is_(False))
        .options(joinedload(ProductionScheduleItem.product), joinedload(ProductionScheduleItem.line), joinedload(ProductionScheduleItem.demand_item))
        .order_by(ProductionScheduleItem.production_date, ProductionScheduleItem.sequence)
    ))
    if allowed_line_name:
        items = [item for item in items if item.line and item.line.name == allowed_line_name]
    statuses = defaultdict(int)
    for item in items:
        statuses[item.status.value] += 1
    return {
        "id": plan.id, "name": plan.name, "status": plan.status.value,
        "horizon_start": plan.horizon_start, "horizon_end": plan.horizon_end,
        "updated_at": plan.updated_at, "version": plan.revision,
        "items": [schedule_item_dict(item) for item in items],
        "summary": {
            "total": len(items), "planned": statuses["planned"] + statuses["warning"],
            "warnings": statuses["warning"], "conflicts": statuses["conflict"],
            "unscheduled": statuses["unscheduled"],
        },
    }
