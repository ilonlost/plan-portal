from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP


@dataclass(frozen=True)
class DemandInput:
    id: int
    product_id: int
    sku: str
    quantity: Decimal
    requested_date: date
    due_date: date
    priority: int = 100
    source_quantity: Decimal | None = None
    source_unit: str = "кг"
    unit_weight_kg: Decimal | None = None
    units_per_box: Decimal | None = None
    box_quantum_kg: Decimal | None = None
    exact_date: bool = False
    source_kind: str = "generic"
    marking_date: date | None = None
    warnings: tuple[str, ...] = ()
    mono_group: str = ""


@dataclass(frozen=True)
class CapabilityInput:
    line_id: int
    product_id: int
    units_per_hour: Decimal
    line_priority: int = 100
    batch_quantum_kg: Decimal | None = None
    min_order_kg: Decimal | None = None
    workshop_code: str = ""


@dataclass(frozen=True)
class CapacityInput:
    line_id: int
    capacity_date: date
    available_hours: Decimal
    available: bool = True
    shift: str = "day"


@dataclass
class PlannedItem:
    demand_id: int
    product_id: int
    line_id: int | None
    production_date: date | None
    quantity: Decimal
    required_hours: Decimal
    status: str
    shift: str = "day"
    source_quantity: Decimal | None = None
    source_unit: str = "кг"
    quantity_kg: Decimal | None = None
    box_count: Decimal | None = None
    batch_count: Decimal | None = None
    source_kind: str = "generic"
    marking_date: date | None = None
    warnings: list[str] = field(default_factory=list)


class PlanningEngine:
    """Finite-capacity planner for exact-date and weekly FK demand.

    Quantities are planned in kg. Daily OHL demand stays on its source date and is
    divisible by the box quantum. Weekly demand never leaves its ISO-week and is
    divisible by the production batch quantum. Day/night slots are balanced by load.
    """

    def plan(
        self,
        demands: list[DemandInput],
        capabilities: list[CapabilityInput],
        capacities: list[CapacityInput],
        horizon_end: date,
    ) -> list[PlannedItem]:
        compatible: dict[int, list[CapabilityInput]] = defaultdict(list)
        for capability in capabilities:
            compatible[capability.product_id].append(capability)
        for values in compatible.values():
            values.sort(key=lambda item: (item.line_priority, -item.units_per_hour, item.line_id))

        capacity_by_slot = {
            (slot.line_id, slot.capacity_date, slot.shift): slot.available_hours if slot.available else Decimal("0")
            for slot in capacities
        }
        shifts_by_day_line: dict[tuple[int, date], list[str]] = defaultdict(list)
        for slot in capacities:
            shifts_by_day_line[(slot.line_id, slot.capacity_date)].append(slot.shift)
        used_hours: dict[tuple[int, date, str], Decimal] = defaultdict(lambda: Decimal("0"))
        last_group: dict[tuple[int, date, str], str] = {}
        seen_groups: dict[tuple[int, date], set[str]] = defaultdict(set)
        result: list[PlannedItem] = []

        source_rank = {"ohl": 0, "generic": 1, "zam": 2}
        for demand in sorted(demands, key=lambda item: (
            source_rank.get(item.source_kind, 1), item.due_date, item.mono_group, item.priority, item.requested_date, item.id,
        )):
            options = compatible.get(demand.product_id, [])
            if not options:
                result.append(self._item(
                    demand, None, None, demand.quantity, Decimal("0"), "unscheduled", "day", None,
                    [*demand.warnings, "Нет совместимой производственной линии"],
                ))
                continue

            quantum = self._quantum(demand, options)
            source_is_exact_kg = demand.source_kind == "ohl" and demand.source_unit == "кг"
            planned_total = demand.quantity if source_is_exact_kg else self._ceil_quantum(demand.quantity, quantum)
            warnings = list(demand.warnings)
            if planned_total > demand.quantity:
                warnings.append(f"Объём округлён с {demand.quantity} до {planned_total} кг по кванту {quantum} кг")
            min_order = min((item.min_order_kg for item in options if item.min_order_kg and item.min_order_kg > 0), default=None)
            if min_order and planned_total < min_order and not source_is_exact_kg:
                old_total = planned_total
                planned_total = self._ceil_quantum(min_order, quantum)
                warnings.append(f"Минимальный заказ: объём увеличен с {old_total} до {planned_total} кг")

            remaining = planned_total
            first_date = demand.requested_date
            last_date = demand.requested_date if demand.exact_date else min(horizon_end, demand.due_date)
            candidates: list[tuple[CapabilityInput, date, str]] = []
            current = first_date
            while current <= last_date:
                for capability in options:
                    for shift in sorted(set(shifts_by_day_line.get((capability.line_id, current), [])), key=self._shift_order):
                        if capacity_by_slot.get((capability.line_id, current, shift), Decimal("0")) > 0:
                            candidates.append((capability, current, shift))
                current += timedelta(days=1)

            while remaining > 0:
                available = []
                for capability, production_date, shift in candidates:
                    key = (capability.line_id, production_date, shift)
                    day_key = (capability.line_id, production_date)
                    capacity = capacity_by_slot[key]
                    # На ПЦ каждый новый блок монопродукта резервирует часовую мойку.
                    # Остальные SKU той же группы используют уже зарезервированную мойку.
                    needs_wash = bool(
                        capability.workshop_code == "PC"
                        and (demand.mono_group or demand.sku) not in seen_groups[day_key]
                    )
                    wash_hours = Decimal("1") if needs_wash else Decimal("0")
                    free_hours = max(Decimal("0"), capacity - used_hours[key] - wash_hours)
                    free_kg = free_hours * capability.units_per_hour
                    free_quantized = free_kg if source_is_exact_kg else self._floor_quantum(free_kg, quantum)
                    can_fit = free_quantized > 0 if source_is_exact_kg else free_quantized >= quantum
                    if can_fit:
                        utilization = used_hours[key] / capacity if capacity else Decimal("999")
                        if capability.workshop_code == "PC":
                            setup_rank = 0 if last_group.get(key) == (demand.mono_group or demand.sku) else 1 if used_hours[key] == 0 else 2
                        else:
                            setup_rank = 0
                        available.append((setup_rank, utilization, production_date, self._shift_order(shift), capability.line_priority, capability, shift, free_quantized, wash_hours))
                if not available:
                    break
                available.sort(key=lambda item: item[:5])
                _, _, production_date, _, _, capability, shift, free_kg, wash_hours = available[0]
                # Keep a batch together when it fits. The next demand will select the
                # least-loaded slot, which balances shifts without fragmenting every SKU.
                quantity = min(remaining, free_kg)
                hours = self._hours(quantity, capability.units_per_hour)
                slot_key = (capability.line_id, production_date, shift)
                used_hours[slot_key] += wash_hours + hours
                last_group[slot_key] = demand.mono_group or demand.sku
                seen_groups[(capability.line_id, production_date)].add(demand.mono_group or demand.sku)
                remaining -= quantity
                result.append(self._item(
                    demand, capability, production_date, quantity, hours,
                    "warning" if warnings else "planned", shift, quantum, warnings.copy(),
                ))

            if remaining > 0:
                best = options[0]
                conflict_date = last_date
                shift = "day"
                existing = shifts_by_day_line.get((best.line_id, conflict_date), [])
                if existing:
                    shift = sorted(set(existing), key=self._shift_order)[0]
                conflict_warnings = warnings.copy()
                if demand.exact_date:
                    conflict_warnings.append("Дата зафиксирована источником ОХЛ; перенос на другой день запрещён")
                conflict_warnings.append("Недостаточно мощности в допустимом производственном окне")
                hours = self._hours(remaining, best.units_per_hour)
                used_hours[(best.line_id, conflict_date, shift)] += hours
                result.append(self._item(
                    demand, best, conflict_date, remaining, hours, "conflict", shift, quantum, conflict_warnings,
                ))
        return result

    def _item(self, demand, capability, production_date, quantity, hours, status, shift, quantum, warnings):
        source_quantity = None
        box_count = None
        if demand.source_unit == "шт" and demand.unit_weight_kg:
            source_quantity = (quantity / demand.unit_weight_kg).quantize(Decimal("0.001"))
        elif demand.source_unit == "кг":
            source_quantity = quantity
        if demand.box_quantum_kg and demand.box_quantum_kg > 0:
            box_count = (quantity / demand.box_quantum_kg).quantize(Decimal("0.001"))
        elif source_quantity is not None and demand.units_per_box:
            box_count = (source_quantity / demand.units_per_box).quantize(Decimal("0.001"))
        batch_count = (quantity / quantum).quantize(Decimal("0.001")) if quantum else None
        return PlannedItem(
            demand_id=demand.id, product_id=demand.product_id,
            line_id=capability.line_id if capability else None, production_date=production_date,
            quantity=quantity, quantity_kg=quantity, source_quantity=source_quantity,
            source_unit=demand.source_unit, box_count=box_count, batch_count=batch_count,
            required_hours=hours, status=status, shift=shift, warnings=warnings,
            source_kind=demand.source_kind, marking_date=demand.marking_date,
        )

    @staticmethod
    def _quantum(demand: DemandInput, options: list[CapabilityInput]) -> Decimal:
        if demand.source_unit == "шт" and demand.box_quantum_kg and demand.box_quantum_kg > 0:
            return demand.box_quantum_kg
        values = [item.batch_quantum_kg for item in options if item.batch_quantum_kg and item.batch_quantum_kg > 0]
        return min(values) if values else Decimal("0.001")

    @staticmethod
    def _ceil_quantum(value: Decimal, quantum: Decimal) -> Decimal:
        if quantum <= 0:
            return value
        return (value / quantum).to_integral_value(rounding=ROUND_CEILING) * quantum

    @staticmethod
    def _floor_quantum(value: Decimal, quantum: Decimal) -> Decimal:
        if quantum <= 0:
            return value
        return (value / quantum).to_integral_value(rounding=ROUND_FLOOR) * quantum

    @staticmethod
    def _hours(quantity: Decimal, speed: Decimal) -> Decimal:
        if quantity <= 0 or speed <= 0:
            return Decimal("0")
        return max(Decimal("0.02"), (quantity / speed).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

    @staticmethod
    def _shift_order(shift: str) -> int:
        return {"day": 0, "night": 1}.get(shift, 2)
