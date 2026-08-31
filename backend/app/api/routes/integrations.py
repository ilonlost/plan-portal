from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.core.security import UserContext, require_planner
from app.db.session import get_db
from app.models.entities import AuditEvent, IntegrationRun, ProductionPlan, ProductionScheduleItem
from app.services.notification_service import send_notification
from app.services.plan_service import schedule_item_dict
from app.services.csb_export_service import build_csb_text


router = APIRouter(prefix="/integrations", tags=["integrations"])


class CsbNextDayRequest(BaseModel):
    target_date: date | None = None


def _production_items(db: Session, plan: ProductionPlan, target: date) -> list[ProductionScheduleItem]:
    return list(db.scalars(
        select(ProductionScheduleItem).where(
            ProductionScheduleItem.plan_id == plan.id,
            ProductionScheduleItem.production_date == target,
            ProductionScheduleItem.excluded.is_(False),
        ).options(
            joinedload(ProductionScheduleItem.product), joinedload(ProductionScheduleItem.line), joinedload(ProductionScheduleItem.demand_item),
        ).order_by(ProductionScheduleItem.line_id, ProductionScheduleItem.shift, ProductionScheduleItem.sequence)
    ))


@router.get("/csb/download")
def download_csb_file(
    target_date: date | None = None, destination: str = "ДМД",
    db: Session = Depends(get_db), user: UserContext = Depends(require_planner),
) -> Response:
    target = target_date or (date.today() + timedelta(days=1))
    plan = db.scalar(select(ProductionPlan).where(ProductionPlan.active.is_(True)).order_by(ProductionPlan.updated_at.desc()))
    if not plan:
        raise HTTPException(404, "Активный план не найден")
    text, exported_ids = build_csb_text(_production_items(db, plan, target), destination)
    if not exported_ids:
        raise HTTPException(422, f"На {target.strftime('%d.%m.%Y')} нет заданий с заполненным кодом линии CSB")
    run = IntegrationRun(
        integration="csb", operation="download_txt", target_date=target,
        status="prepared", test_mode=settings.csb_test_mode, item_count=len(exported_ids),
        payload={"plan_id": plan.id, "destination": destination, "item_ids": exported_ids},
        response={"accepted": True, "mode": "file", "message": "TXT-файл подготовлен"}, created_by=user.username,
    )
    db.add(run)
    db.add(AuditEvent(username=user.username, action="csb_txt_downloaded", entity_type="production_plan", entity_id=str(plan.id), details={"target_date": target.isoformat(), "item_count": len(exported_ids), "destination": destination}))
    db.commit()
    filename = f"Задание CSB {target.strftime('%d.%m.%Y')}.txt"
    return Response(text.encode("utf-8-sig"), media_type="text/plain; charset=utf-8", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


@router.post("/csb/next-day")
def send_next_day_to_csb(
    payload: CsbNextDayRequest,
    db: Session = Depends(get_db),
    user: UserContext = Depends(require_planner),
) -> dict:
    target = payload.target_date or (date.today() + timedelta(days=1))
    plan = db.scalar(select(ProductionPlan).where(ProductionPlan.active.is_(True)).order_by(ProductionPlan.updated_at.desc()))
    if not plan:
        raise HTTPException(404, "Активный план не найден")
    items = _production_items(db, plan, target)
    production_items = [item for item in items if item.schedule_kind == "production"]
    if not production_items:
        raise HTTPException(422, f"На {target.strftime('%d.%m.%Y')} нет производственных заданий")

    tasks = []
    for item in production_items:
        serialized = schedule_item_dict(item)
        tasks.append({
            "task_id": item.id,
            "production_date": target.isoformat(),
            "marking_date": item.marking_date.isoformat() if item.marking_date else None,
            "workshop": item.line.workshop_code if item.line else None,
            "line": item.line.name if item.line else None,
            "shift": item.shift,
            "sequence": item.sequence,
            "sku": item.product.sku if item.product else None,
            "product_name": item.product.name if item.product else None,
            "quantity_kg": float(item.quantity_kg or item.quantity or 0),
            "quantity_units": float(serialized["quantity_units"]) if serialized["quantity_units"] is not None else None,
            "source": item.source_kind,
        })
    request_payload = {"plan_id": plan.id, "target_date": target.isoformat(), "tasks": tasks}
    response_payload = {
        "accepted": True,
        "mode": "test" if settings.csb_test_mode else "configured",
        "message": "Тестовое задание сформировано. Передача во внешнюю CSB не выполнялась.",
    }
    run = IntegrationRun(
        integration="csb", operation="next_day_production_job", target_date=target,
        status="test_prepared" if settings.csb_test_mode else "prepared",
        test_mode=settings.csb_test_mode, item_count=len(tasks), payload=request_payload,
        response=response_payload, created_by=user.username,
    )
    db.add(run)
    db.add(AuditEvent(
        username=user.username, action="csb_next_day_prepared", entity_type="production_plan",
        entity_id=str(plan.id), details={"target_date": target.isoformat(), "item_count": len(tasks), "test_mode": settings.csb_test_mode},
    ))
    db.commit()
    db.refresh(run)
    send_notification(
        db, "csb_next_day_prepared", f"PLAN Portal: задание CSB на {target.strftime('%d.%m.%Y')}",
        f"Пользователь {user.display_name} подготовил {'тестовое ' if settings.csb_test_mode else ''}задание для CSB.\nПозиций: {len(tasks)}.\nПлан: {plan.name}.",
    )
    return {"run_id": run.id, "target_date": target, "item_count": len(tasks), "status": run.status, "response": response_payload}
