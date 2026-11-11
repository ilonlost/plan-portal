from datetime import date, datetime, time, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import JSON, String, cast, func, or_, select
from sqlalchemy.orm import Session

from app.core.security import require_admin
from app.db.session import get_db
from app.models.entities import AuditEvent, Product, LineCapability
from app.services.admin_inspection_service import TABLES, table_columns, redact_details

router = APIRouter(dependencies=[Depends(require_admin)])
MOSCOW = timezone(timedelta(hours=3))


def text_search(columns, query):
    return or_(*(cast(column, String).icontains(query, autoescape=True) for column in columns))


@router.get("/audit-log")
def audit_log(
    q: str = Query(default="", max_length=200), action: str = Query(default="", max_length=80),
    username: str = Query(default="", max_length=120), entity_type: str = Query(default="", max_length=80),
    start: date | None = None, end: date | None = None,
    offset: int = Query(default=0, ge=0, le=1000000), limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    if start and end and start > end:
        raise HTTPException(422, "Дата начала должна быть не позже даты окончания")
    filters = []
    for column, value in ((AuditEvent.action, action), (AuditEvent.username, username), (AuditEvent.entity_type, entity_type)):
        if value:
            filters.append(column == value)
    if start:
        filters.append(AuditEvent.created_at >= datetime.combine(start, time.min, MOSCOW).astimezone(timezone.utc))
    if end:
        if end == date.max:
            raise HTTPException(422, "Дата окончания вне допустимого диапазона")
        filters.append(AuditEvent.created_at < datetime.combine(end + timedelta(days=1), time.min, MOSCOW).astimezone(timezone.utc))
    if q.strip():
        needle = q.strip()
        products = select(cast(Product.id, String)).where(text_search([Product.sku, Product.name], needle))
        capabilities = select(cast(LineCapability.id, String)).join(Product).where(text_search([Product.sku, Product.name], needle))
        filters.append(or_(
            text_search([AuditEvent.username, AuditEvent.action, AuditEvent.entity_type, AuditEvent.entity_id, AuditEvent.details], needle),
            (AuditEvent.entity_type == "product") & AuditEvent.entity_id.in_(products),
            (AuditEvent.entity_type == "line_capability") & AuditEvent.entity_id.in_(capabilities),
        ))
    rows = db.scalars(select(AuditEvent).where(*filters).order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc()).offset(offset).limit(limit))
    return {"total": db.scalar(select(func.count()).select_from(AuditEvent).where(*filters)), "offset": offset, "limit": limit,
            "rows": [{field: redact_details(getattr(row, field)) for field in ("id", "created_at", "username", "action", "entity_type", "entity_id", "details")} for row in rows]}


@router.get("/audit-log/filters")
def audit_filters(db: Session = Depends(get_db)):
    return {name: list(db.scalars(select(column).distinct().order_by(column))) for name, column in
            (("actions", AuditEvent.action), ("users", AuditEvent.username), ("entities", AuditEvent.entity_type))}


@router.get("/database")
def database_tables():
    return {"read_only": True, "tables": [{"name": name, "label": label,
             "columns": [{"name": field, "type": str(column.type)} for field, column in table_columns(name).items()]}
             for name, (label, _, _) in TABLES.items()]}


@router.get("/database/{table_name}")
def database_rows(
    table_name: str, q: str = Query(default="", max_length=200),
    column: str = "", value: str = Query(default="", max_length=200),
    sort: str = "id", direction: Literal["asc", "desc"] = "desc",
    offset: int = Query(default=0, ge=0, le=1000000), limit: int = Query(default=50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    if table_name not in TABLES:
        raise HTTPException(404, "Таблица недоступна для просмотра")
    columns = table_columns(table_name)
    if sort not in columns or (column and column not in columns):
        raise HTTPException(422, "Неизвестный столбец")
    filters = []
    if q.strip():
        filters.append(text_search(columns.values(), q.strip()))
    if column and value != "":
        filters.append(cast(columns[column], String) == value)
    model = TABLES[table_name][1]
    sort_column = cast(columns[sort], String) if isinstance(columns[sort].type, JSON) else columns[sort]
    order = sort_column.asc() if direction == "asc" else sort_column.desc()
    rows = db.execute(select(*columns.values()).where(*filters).order_by(order, model.id.desc()).offset(offset).limit(limit)).mappings()
    return {"table": table_name, "read_only": True, "offset": offset, "limit": limit,
            "total": db.scalar(select(func.count()).select_from(model).where(*filters)),
            "rows": [redact_details(dict(row)) for row in rows]}
