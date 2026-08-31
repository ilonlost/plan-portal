import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

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


@app.on_event("startup")
def correct_legacy_ohl_source_units() -> None:
    """Apply the one-time kg correction to plans imported before this release."""
    db = SessionLocal()
    try:
        corrected = PlanService(db).correct_legacy_ohl_source_units()
        if corrected:
            logger.warning("Исправлены единицы измерения ОХЛ: %s строк(и), план пересчитан", corrected)
    finally:
        db.close()
