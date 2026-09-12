import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm.exc import StaleDataError

from app.api.router import api_router
from app.core.config import settings
from app.db.session import SessionLocal
from app.services.plan_service import PlanService


logger = logging.getLogger(__name__)

app = FastAPI(title=settings.app_name, version="0.1.0", docs_url="/api/docs", openapi_url="/api/openapi.json")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router, prefix="/api")


@app.exception_handler(StaleDataError)
async def stale_plan(request, exc):
    return JSONResponse(status_code=409, content={"detail": "План уже изменён другим пользователем. Ваши изменения не записаны. Обновите план и повторите корректировку."})


@app.on_event("startup")
def correct_legacy_ohl_source_units() -> None:
    """Apply the one-time kg correction to plans imported before this release."""
    db = SessionLocal()
    try:
        if not settings.local_auth_enabled:
            if settings.auth_mode != "ldap" or len(settings.session_secret) < 32 or settings.session_secret == "local-development-secret-change-in-production":
                raise RuntimeError("Production требует AUTH_MODE=ldap и собственный SESSION_SECRET (не менее 32 символов)")
            from sqlalchemy import select
            from app.core.security import LOCAL_USER_MARKER
            from app.models.entities import User
            for user in db.scalars(select(User)):
                if user.username.lower() in {"demo.admin", "demo.planner"} or LOCAL_USER_MARKER in (user.ldap_groups or []):
                    user.active = False
            db.commit()
        service = PlanService(db)
        corrected = service.correct_legacy_ohl_source_units()
        if corrected:
            logger.warning("Исправлены единицы измерения ОХЛ: %s строк(и), план пересчитан", corrected)
        rounded = service.apply_kg_rounding_upgrade()
        if rounded:
            logger.warning("План пересчитан с округлением кг вверх до целого значения")
        packaged = service.apply_piece_and_box_rounding_upgrade()
        if packaged:
            logger.warning("План пересчитан: штуки округлены вверх, задания приведены к полным коробам")
        line_rules = service.apply_line_planning_rules_upgrade()
        if line_rules:
            logger.warning("Активный план пересчитан с правилами запуска и переходов по линиям")
    finally:
        db.close()
