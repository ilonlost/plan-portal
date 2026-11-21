from datetime import date
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from starlette.background import BackgroundTask

from app.core.security import UserContext, current_user
from app.services.tail_buffer_service import CENTERS, SourceError, load_summary, load_tail_buffers
from app.services.production_fact_export import export_workbook
from app.services import production_eol, shipment_fact

router = APIRouter(prefix="/production-fact", tags=["production-fact"])
MAX_RANGE_DAYS = 184


@router.get("/shipment/report")
def shipment_report(start: date | None = None, end: date | None = None,
                    center: str = Query("", pattern=r"^\d{0,9}$"), search: str = Query("", max_length=120),
                    offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500),
                    _: UserContext = Depends(current_user)):
    start, end = _range(start, end)
    try:
        data = shipment_fact.report(start, end, center, search)
    except SourceError as error:
        raise HTTPException(503, "; ".join(error.issues)) from None
    data["rows"] = data["rows"][offset:offset + limit]
    return {**data, "offset": offset, "limit": limit}


@router.get("/shipment/report/export.xlsx")
def shipment_export(start: date | None = None, end: date | None = None,
                    center: str = Query("", pattern=r"^\d{0,9}$"), search: str = Query("", max_length=120),
                    _: UserContext = Depends(current_user)):
    start, end = _range(start, end)
    try:
        data = shipment_fact.report(start, end, center, search)
        data.update(filter_center=center, filter_search=search)
        content = production_eol.export_xlsx(data)
    except SourceError as error:
        raise HTTPException(503, "; ".join(error.issues)) from None
    filename = quote(f"Факт отгрузки {start} — {end}.xlsx")
    return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"})


@router.get("/report")
def production_eol_report(start: date | None = None, end: date | None = None,
                          center: str = Query("", pattern=r"^\d{0,9}$"), search: str = Query("", max_length=120),
                          offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500),
                          _: UserContext = Depends(current_user)):
    start, end = _range(start, end)
    try:
        data = production_eol.report(start, end, center, search)
    except SourceError as error:
        raise HTTPException(503, "; ".join(error.issues)) from None
    data["rows"] = data["rows"][offset:offset + limit]
    return {**data, "offset": offset, "limit": limit}


@router.get("/report/export.xlsx")
def export_production_eol(start: date | None = None, end: date | None = None,
                          center: str = Query("", pattern=r"^\d{0,9}$"), search: str = Query("", max_length=120),
                          _: UserContext = Depends(current_user)):
    start, end = _range(start, end)
    try:
        data = production_eol.report(start, end, center, search)
        data.update(filter_center=center, filter_search=search)
        content = production_eol.export_xlsx(data)
    except SourceError as error:
        raise HTTPException(503, "; ".join(error.issues)) from None
    filename = quote(f"Факт производства {start} — {end}.xlsx")
    return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"})


def _range(start: date | None, end: date | None):
    start = start or date.today()
    end = end or start
    if end < start:
        raise HTTPException(422, "Конечная дата не может быть раньше начальной")
    if (end - start).days + 1 > MAX_RANGE_DAYS:
        raise HTTPException(422, "Можно выбрать период не более шести месяцев (184 дня)")
    return start, end


def _center(center: str):
    if center and center not in {item[1] for item in CENTERS}:
        raise HTTPException(422, "Неизвестное место затрат")
    return center


@router.get("")
def production_fact(start: date | None = None, end: date | None = None, _: UserContext = Depends(current_user)):
    return load_summary(*_range(start, end))


@router.get("/tail-buffers")
def tail_buffers(start: date | None = None, end: date | None = None,
                 offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500),
                 center: str = "", search: str = Query("", max_length=120), _: UserContext = Depends(current_user)):
    return load_tail_buffers(*_range(start, end), offset=offset, limit=limit, center=_center(center), search=search)


@router.get("/cost-centers/{code}")
def production_fact_detail(code: str, start: date | None = None, end: date | None = None,
                           offset: int = Query(0, ge=0), _: UserContext = Depends(current_user)):
    return load_tail_buffers(*_range(start, end), center=_center(code), offset=offset)


@router.get("/export.xlsx")
def export_production_fact(start: date | None = None, end: date | None = None,
                           center: str = "", search: str = Query("", max_length=120), _: UserContext = Depends(current_user)):
    start, end = _range(start, end)
    try:
        path = export_workbook(start, end, _center(center), search)
    except SourceError as error:
        raise HTTPException(503, "Выгрузка не создана: " + "; ".join(error.issues)) from None
    return FileResponse(path, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        filename=f"Факт буферов {start:%d.%m.%Y}-{end:%d.%m.%Y}.xlsx",
                        background=BackgroundTask(Path(path).unlink, missing_ok=True))
