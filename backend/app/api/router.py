from fastapi import APIRouter

from app.api.routes import admin, catalog, dashboard, feedback, imports, integrations, lines, plans, session

api_router = APIRouter()
api_router.include_router(dashboard.router)
api_router.include_router(plans.router)
api_router.include_router(lines.router)
api_router.include_router(imports.router)
api_router.include_router(catalog.router)
api_router.include_router(session.router)
api_router.include_router(feedback.router)
api_router.include_router(admin.router)
api_router.include_router(integrations.router)
