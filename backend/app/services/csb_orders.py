import re
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.entities import CsbSubmission, ProductionScheduleItem
from app.services.csb_export_service import DESTINATION_CODES, _decimal, _integer, lock_csb_counter


def bom_query(sku: str, variant: str, center: str, kilograms: str) -> str:
    if not all(re.fullmatch(r"[0-9]+", value) for value in (sku, variant, center)):
        raise ValueError("Артикул, вариант спецификации и МЗ должны состоять из цифр")
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,6})?", kilograms) or Decimal(kilograms) <= 0:
        raise ValueError("Вес должен быть положительным числом с точностью до шести знаков после запятой")
    return f'DT0061+MAIN;?:T1+{sku}:T2+{variant}:F810+"T14+,T17+"'


def verify_bom_reply(transport: int, status: str, rows: Sequence[str], center: str) -> None:
    if transport != 1 or ":L2+0+0+0:" not in status:
        raise ValueError("CSB не подтвердила проверку спецификации")
    records = [row for row in rows if row]
    if len(records) != 1:
        raise ValueError("Ожидалась одна спецификация CSB")
    fields = records[0].split(":")
    centers = [field[4:] for field in fields if field.startswith("T14+")]
    flags = [field[4:] for field in fields if field.startswith("T17+")]
    if len(centers) != 1 or not re.fullmatch(r"[0-9]+", centers[0]) or flags not in (["0"], ["1"]):
        raise ValueError("Некорректные поля МЗ или блокировки в ответе CSB")
    if flags == ["1"]:
        raise ValueError("Спецификация заблокирована в CSB")
    if centers[0] != center:
        raise ValueError(f"МЗ спецификации {centers[0]} отличается от МЗ задания {center}")


def record_order_confirmation(db: Session, expected_t63: int, returned_t63: int, order_number: str) -> None:
    """Record a verified CSB receipt in the caller's transaction."""
    if returned_t63 != expected_t63 or not 1 <= returned_t63 <= 99999:
        raise ValueError("T63 в подтверждении CSB отличается от отправленного")
    order_number = order_number.strip()
    if not re.fullmatch(r"[0-9]+", order_number) or not order_number.strip("0"):
        raise ValueError("В подтверждении CSB отсутствует номер задания")
    query = select(ProductionScheduleItem).where(ProductionScheduleItem.csb_task_number == returned_t63)
    item = db.scalar(query.with_for_update().execution_options(populate_existing=True))
    if item is None:
        raise ValueError(f"Задание с T63={returned_t63} отсутствует в плане")
    if item.csb_order_number is not None and item.csb_order_number != order_number:
        raise ValueError(f"Для T63={returned_t63} уже подтверждён другой номер CSB")
    item.csb_order_number = order_number


def order_payload(item: ProductionScheduleItem) -> dict:
    if item.excluded or item.schedule_kind != "production" or not item.product or not item.line or not item.production_date:
        raise ValueError("Задание больше не доступно для выгрузки")
    center, sku, variant = item.line.csb_line_code or "", item.product.sku, item.line.csb_t5 or "4"
    weight = _decimal(item.quantity_kg or item.quantity or 0)
    bom_query(sku, variant, center, weight)
    priority = f"{2 if item.shift == 'night' else 1}{int(item.sequence or 0):03d}"
    if not priority.isascii() or not priority.isdigit():
        raise ValueError("Некорректная очередность задания CSB")
    return dict(Center=center, PlanDate=item.production_date.strftime("%Y%m%d"), Item=sku,
                Variant=variant, WeightKg=weight, Customer=DESTINATION_CODES["ДМД"],
                Priority=priority, Contract=_integer((item.line.csb_t55 or "").strip() or item.required_hours),
                Promotion=str(item.csb_task_number))


def queue_order(db: Session, item: ProductionScheduleItem, username: str) -> CsbSubmission:
    job = db.get(CsbSubmission, item.csb_task_number)
    if job and job.state not in {"failed", "review"}:
        return job
    now = datetime.now(timezone.utc)
    if not job:
        job = CsbSubmission(t63=item.csb_task_number, requested_by=username, updated_at=now,
                            state="queued", payload=order_payload(item))
        db.add(job)
    elif job.state == "review":
        # A previous write may have reached MERP. Only look up its original scope.
        job.state = "lookup"
    else:
        job.state = "queued"
        job.payload = order_payload(item)
    job.requested_by, job.detail, job.updated_at = username, None, now
    if item.csb_order_number:
        job.state, job.order_number = "confirmed", item.csb_order_number
    return job


def claim_order(db: Session) -> dict | None:
    lock_csb_counter(db)
    now = datetime.now(timezone.utc)
    active = db.scalar(select(CsbSubmission).where(CsbSubmission.state == "processing").limit(1))
    if active:
        if active.updated_at.replace(tzinfo=timezone.utc) > now - timedelta(hours=1):
            db.commit()
            return None
        active.state, active.claim = "lookup", None
        db.flush()
    queued = select(CsbSubmission).where(CsbSubmission.state.in_(("queued", "lookup"))).order_by(CsbSubmission.updated_at, CsbSubmission.t63).limit(1)
    while (job := db.scalar(queued)) is not None:
        lookup = job.state == "lookup"
        if not lookup:
            item = db.scalar(select(ProductionScheduleItem).where(ProductionScheduleItem.csb_task_number == job.t63))
            try:
                if item is None or not item.plan.active or order_payload(item) != job.payload:
                    raise ValueError("Задание изменилось после постановки в очередь")
            except ValueError as exc:
                job.state, job.detail, job.updated_at = "failed", str(exc), now
                db.flush()
                continue
        job.state, job.updated_at, job.claim = "processing", now, str(uuid4())
        result = dict(t63=job.t63, claim=job.claim, action="Lookup" if lookup else "Submit", parameters=job.payload)
        db.commit()
        return result
    db.commit()
    return None
