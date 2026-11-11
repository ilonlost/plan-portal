"""One-time, user-authorized analogue mappings. Dry run unless --apply is passed.

Run in the backend environment (PYTHONPATH=/app). Uses an outer transaction so
the planner's internal commits cannot partially apply this repair.
"""
import json
import sys
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import engine
from app.models.entities import AuditEvent, LineCapability, Product, ProductionLine, ProductionPlan
from app.services.plan_service import PlanService


RULES = [
    ("1010031324", "1010031474", "Миквак", "322.00"),
    ("1010036438", "1010028789", "Ручная зона ПЦ", "78.00"),
    ("1010036437", "1010028789", "Ручная зона ПЦ", "78.00"),
    ("1010027564", "1010028789", "Ручная зона ПЦ", "78.00"),
    ("1010035914", "1010025512", "Хлеба", "597.60"),
]
FIELDS = ("units_per_hour", "speed_unit", "batch_quantum_kg", "min_order_kg", "min_batch", "max_batch", "capacity_type", "restrictions")


def fields(cap):
    return {key: str(value) if isinstance(value := getattr(cap, key), Decimal) else value for key in FIELDS}


def repair(apply=False):
    with engine.connect() as connection:
        outer = connection.begin()
        try:
            with Session(bind=connection, expire_on_commit=False, join_transaction_mode="create_savepoint") as db:
                plan = db.scalar(select(ProductionPlan).where(ProductionPlan.active.is_(True)).with_for_update())
                assert plan is not None, "No active plan"
                manual_fields = ("id", "product_id", "line_id", "production_date", "marking_date", "quantity", "excluded", "execution_status", "actual_quantity_kg", "reported_at", "parent_task_id")
                manual_before = {row.id: tuple(getattr(row, f) for f in manual_fields) for row in plan.schedule_items if row.source != "auto"}
                changes = []
                targets = []
                for sku, analogue_sku, line_name, expected_speed in RULES:
                    target = db.scalar(select(Product).where(Product.sku == sku).with_for_update())
                    analogue = db.scalar(select(Product).where(Product.sku == analogue_sku))
                    line = db.scalar(select(ProductionLine).where(ProductionLine.name == line_name, ProductionLine.status == "active"))
                    assert target and analogue and line and target.active and target.catalog_status == "active", sku
                    source = db.scalar(select(LineCapability).where(LineCapability.product_id == analogue.id, LineCapability.line_id == line.id))
                    assert source and source.units_per_hour == Decimal(expected_speed), f"Analogue changed: {analogue_sku}"
                    targets.append(target.id)
                    existing = list(db.scalars(select(LineCapability).where(LineCapability.product_id == target.id)))
                    active = [c for c in existing if db.get(ProductionLine, c.line_id).status == "active"]
                    if active:
                        assert len(active) == 1 and active[0].line_id == line.id and fields(active[0]) == fields(source), f"Concurrent mapping for {sku}; inspect before retry"
                        continue
                    before = {"state": target.state, "capabilities": [{"id": c.id, "line_id": c.line_id, **fields(c)} for c in existing]}
                    capability = LineCapability(product_id=target.id, line_id=line.id, **{f: getattr(source, f) for f in FIELDS})
                    db.add(capability)
                    if target.state is None:
                        target.state = "ЗАМ"
                    db.flush()
                    detail = {"sku": sku, "product_name": target.name, "analogue_sku": analogue_sku,
                              "analogue_name": analogue.name, "line_name": line.name,
                              "reason": "Заполнение по аналогичным артикулам по поручению пользователя от 11.11.2026; фасовка сохранена",
                              "before": before, "after": {"state": target.state, "capability_id": capability.id, "line_id": line.id, **fields(capability)}}
                    db.add(AuditEvent(username="codex:authorized-catalog-repair", action="catalog_analogue_applied", entity_type="product", entity_id=str(target.id), details=detail))
                    changes.append(detail)
                if changes:
                    service = PlanService(db)
                    db.flush()
                    service.calculate(plan, service._latest_source_demands(), "catalog_analogue_applied")
                manual_after = {row.id: tuple(getattr(row, f) for f in manual_fields) for row in plan.schedule_items if row.source != "auto"}
                assert manual_before == manual_after, "Manual tasks or execution facts changed"
                rows = [row for row in plan.schedule_items if row.product_id in targets and not row.excluded and row.schedule_kind == "production"]
                assert rows and not any("Нет совместимой производственной линии" in (row.warnings or []) for row in rows)
                result = {"mode": "apply" if apply else "dry_run", "changed": len(changes), "plan_id": plan.id, "revision": plan.revision,
                          "changes": changes, "tasks": [{"sku": db.get(Product, row.product_id).sku, "line": db.get(ProductionLine, row.line_id).name if row.line_id else None,
                          "date": row.production_date, "quantity": row.quantity, "status": row.status.value, "warnings": row.warnings} for row in rows],
                          "manual_tasks_preserved": len(manual_before)}
                db.commit()
                if apply:
                    outer.commit()
                else:
                    outer.rollback()
                return result
        except BaseException:
            if outer.is_active:
                outer.rollback()
            raise


if __name__ == "__main__":
    print(json.dumps(repair("--apply" in sys.argv), ensure_ascii=False, default=str))
