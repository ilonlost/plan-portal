from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from secrets import compare_digest
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.config import settings
from app.core.security import UserContext, require_planner
from app.db.session import get_db
from app.models.entities import AuditEvent, CsbSubmission, IntegrationRun, ProductionPlan, ProductionScheduleItem
from app.services.notification_service import send_notification
from app.services.plan_service import PlanService, schedule_item_dict
from app.services.csb_export_service import build_csb_text, lock_csb_counter
from app.services.csb_orders import claim_order, queue_order, record_order_confirmation


router = APIRouter(prefix="/integrations", tags=["integrations"])


class CsbNextDayRequest(BaseModel):
    target_date: date | None = None


def _production_items(db: Session, plan: ProductionPlan, start: date, end: date | None = None) -> list[ProductionScheduleItem]:
    finish = end or start
    return list(db.scalars(
        select(ProductionScheduleItem).where(
            ProductionScheduleItem.plan_id == plan.id,
            ProductionScheduleItem.production_date >= start,
            ProductionScheduleItem.production_date <= finish,
            ProductionScheduleItem.excluded.is_(False),
        ).options(
            joinedload(ProductionScheduleItem.product), joinedload(ProductionScheduleItem.line), joinedload(ProductionScheduleItem.demand_item),
        ).order_by(ProductionScheduleItem.production_date, ProductionScheduleItem.line_id, ProductionScheduleItem.shift, ProductionScheduleItem.sequence, ProductionScheduleItem.id)
    ))


def _job_view(job: CsbSubmission) -> dict:
    return dict(t63=job.t63, sku=job.payload["Item"], state=job.state, order_number=job.order_number, detail=job.detail)


@router.post("/csb/orders")
def submit_csb_orders(start_date: date, end_date: date, db: Session = Depends(get_db), user: UserContext = Depends(require_planner)):
    if settings.csb_test_mode or not settings.csb_token:
        raise HTTPException(503, "Отправка в рабочую CSB ещё не настроена")
    if end_date < start_date:
        raise HTTPException(422, "Конечная дата раньше начальной")
    counter = lock_csb_counter(db)
    plan = db.scalar(select(ProductionPlan).where(ProductionPlan.active.is_(True)).order_by(ProductionPlan.updated_at.desc()).with_for_update())
    if not plan:
        raise HTTPException(404, "Активный план не найден")
    items = _production_items(db, plan, start_date, end_date)
    try:
        _, ids = build_csb_text(items, counter)
        included = set(ids)
        if not included:
            raise ValueError("За выбранные даты нет заданий для CSB")
        jobs = [queue_order(db, item, user.username) for item in items if item.id in included]
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    run = IntegrationRun(integration="csb", operation="submit_orders", target_date=start_date, status="requested",
                         test_mode=False, item_count=len(jobs), payload={"t63": [job.t63 for job in jobs]}, created_by=user.username)
    db.add(run)
    db.commit()
    return {"run_id": run.id, "jobs": [_job_view(job) for job in jobs]}


@router.get("/csb/orders/{run_id}")
def csb_order_status(run_id: int, db: Session = Depends(get_db), user: UserContext = Depends(require_planner)):
    run = db.get(IntegrationRun, run_id)
    if not run or run.operation != "submit_orders":
        raise HTTPException(404, "Выгрузка не найдена")
    jobs = db.scalars(select(CsbSubmission).where(CsbSubmission.t63.in_(run.payload["t63"])).order_by(CsbSubmission.t63))
    return {"run_id": run.id, "jobs": [_job_view(job) for job in jobs]}


def _worker_auth(authorization: str = Header(default="")):
    if not settings.csb_token or not compare_digest(authorization.encode(), f"Bearer {settings.csb_token}".encode()):
        raise HTTPException(403, "Нет доступа к очереди CSB")


@router.post("/csb/worker/claim", dependencies=[Depends(_worker_auth)])
def csb_worker_claim(db: Session = Depends(get_db)):
    return {"job": None if settings.csb_test_mode else claim_order(db)}


class CsbReceipt(BaseModel):
    claim: str
    returned_t63: int | None = None
    order_number: str | None = Field(default=None, pattern=r"^[0-9]{1,40}$")
    detail: str = Field(default="", max_length=500)
    retry_safe: bool = False


@router.post("/csb/worker/{t63}/receipt", dependencies=[Depends(_worker_auth)])
def csb_worker_receipt(t63: int, body: CsbReceipt, db: Session = Depends(get_db)):
    lock_csb_counter(db)
    job = db.get(CsbSubmission, t63)
    if job is None or job.claim != body.claim:
        raise HTTPException(409, "Попытка отправки устарела")
    if job.state != "processing":
        if job.state == "confirmed" and body.order_number == job.order_number and body.returned_t63 == t63:
            return _job_view(job)
        raise HTTPException(409, "Попытка отправки завершена")
    if body.order_number is not None:
        try:
            record_order_confirmation(db, t63, body.returned_t63, body.order_number)
        except ValueError as exc:
            job.state, job.detail = "review", str(exc)
        else:
            job.state, job.order_number, job.detail = "confirmed", body.order_number, None
    else:
        job.state = "failed" if body.retry_safe else "review"
        job.detail = body.detail or "CSB не подтвердила номер задания"
    job.updated_at = datetime.now(timezone.utc)
    db.add(AuditEvent(username=job.requested_by, action="csb_order_receipt", entity_type="csb_submission",
                      entity_id=str(t63), details={"state": job.state, "order_number": job.order_number}))
    db.commit()
    return _job_view(job)


@router.get("/csb/download")
@router.post("/csb/download")
def download_csb_file(
    target_date: date | None = None, start_date: date | None = None, end_date: date | None = None, destination: str = "ДМД",
    db: Session = Depends(get_db), user: UserContext = Depends(require_planner),
) -> Response:
    start = start_date or target_date or (date.today() + timedelta(days=1))
    end = end_date or start
    if end < start:
        raise HTTPException(422, "Конечная дата выгрузки CSB не может быть раньше начальной")
    counter = lock_csb_counter(db)
    plan = db.scalar(select(ProductionPlan).where(ProductionPlan.active.is_(True)).order_by(ProductionPlan.updated_at.desc()).with_for_update())
    if not plan:
        raise HTTPException(404, "Активный план не найден")
    items = _production_items(db, plan, start, end)
    try:
        text, exported_ids = build_csb_text(items, counter, destination)
    except ValueError as error:
        db.rollback()
        raise HTTPException(422, str(error)) from error
    if not exported_ids:
        period = start.strftime('%d.%m.%Y') if start == end else f"{start.strftime('%d.%m.%Y')} — {end.strftime('%d.%m.%Y')}"
        raise HTTPException(422, f"За {period} нет заданий с заполненным кодом линии CSB")
    content = text.encode("utf-8-sig")
    exported_set = set(exported_ids)
    changed_ids = []
    for item in items:
        if item.id in exported_set and item.execution_status == "not_started":
            item.execution_status = "exported"
            item.reported_by = user.username
            item.reported_at = datetime.now(timezone.utc)
            changed_ids.append(item.id)
    if changed_ids:
        PlanService(db).create_version(plan, "csb_export", f"Выгружено в файл CSB: {len(changed_ids)} заданий")
    run = IntegrationRun(
        integration="csb", operation="download_txt", target_date=start,
        status="prepared", test_mode=settings.csb_test_mode, item_count=len(exported_ids),
        payload={"plan_id": plan.id, "destination": destination, "start_date": start.isoformat(), "end_date": end.isoformat(), "item_ids": exported_ids},
        response={"accepted": True, "mode": "file", "message": "TXT-файл подготовлен"}, created_by=user.username,
    )
    db.add(run)
    db.add(AuditEvent(username=user.username, action="csb_txt_downloaded", entity_type="production_plan", entity_id=str(plan.id), details={"start_date": start.isoformat(), "end_date": end.isoformat(), "item_count": len(exported_ids), "destination": destination, "item_ids": exported_ids, "status_changed_ids": changed_ids}))
    db.commit()
    suffix = start.strftime('%d.%m.%Y') if start == end else f"{start.strftime('%d.%m.%Y')}-{end.strftime('%d.%m.%Y')}"
    filename = f"Задание CSB {suffix}.txt"
    return Response(content, media_type="text/plain; charset=utf-8", headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}", "Cache-Control": "no-store"})


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
