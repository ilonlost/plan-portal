from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth_service import authenticate_ldap
from app.core.config import settings
from app.core.security import DEMO_USERS, UserContext, create_session_token, current_user
from app.db.session import get_db
from app.models.entities import AuthAuditEvent, User


router = APIRouter(prefix="/session", tags=["session"])


class LoginRequest(BaseModel):
    username: str
    password: str


def _user_dict(user: UserContext) -> dict:
    return {
        "username": user.username, "display_name": user.display_name, "role": user.role, "email": user.email,
        "workshop_code": user.workshop_code, "line_name": user.line_name,
        "access_label": {"admin": "Администратор", "planner": "Планирование", "master": "Мастер линии", "viewer": "Просмотр"}.get(user.role, user.role),
    }


@router.post("/login")
def login(payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    login_name = payload.username.strip()
    try:
        if settings.auth_mode.lower() == "ldap":
            identity = authenticate_ldap(login_name, payload.password)
            context = UserContext(identity.username, identity.display_name, identity.role, identity.email)
            groups = identity.groups
        else:
            if payload.password != settings.mock_password or login_name not in DEMO_USERS:
                raise ValueError("Неверный логин или пароль")
            context = DEMO_USERS[login_name]
            groups = []
        stored = db.scalar(select(User).where(User.username == context.username))
        if not stored:
            stored = User(
                username=context.username, display_name=context.display_name, role=context.role, email=context.email,
                workshop_code=context.workshop_code, line_name=context.line_name, ldap_groups=groups,
            )
            db.add(stored)
        else:
            stored.display_name = context.display_name
            stored.email = context.email or stored.email
            stored.ldap_groups = groups
            if settings.auth_mode.lower() == "mock":
                stored.role, stored.workshop_code, stored.line_name = context.role, context.workshop_code, context.line_name
        stored.last_login_at = datetime.now(timezone.utc)
        resolved = UserContext(
            stored.username, stored.display_name, stored.role, stored.email or "", stored.workshop_code, stored.line_name,
        )
        db.add(AuthAuditEvent(
            username=resolved.username, display_name=resolved.display_name, success=True,
            ip_address=request.client.host if request.client else None, user_agent=request.headers.get("user-agent"),
            auth_method=settings.auth_mode.lower(),
        ))
        db.commit()
        response.set_cookie(
            settings.session_cookie_name, create_session_token(resolved), max_age=settings.session_max_age_seconds,
            httponly=True, secure=settings.session_cookie_secure, samesite=settings.session_cookie_samesite.lower(), path="/",
        )
        return {"user": _user_dict(resolved), "auth_mode": settings.auth_mode.lower()}
    except ValueError as exc:
        db.add(AuthAuditEvent(
            username=login_name, success=False, ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"), auth_method=settings.auth_mode.lower(), failure_reason=str(exc),
        ))
        db.commit()
        raise HTTPException(401, str(exc)) from exc


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(settings.session_cookie_name, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: UserContext = Depends(current_user)) -> dict:
    return {**_user_dict(user), "auth_mode": settings.auth_mode.lower()}


@router.get("/mode")
def mode() -> dict:
    return {"auth_mode": settings.auth_mode.lower(), "mock_hint": "demo.admin / demo" if settings.auth_mode.lower() == "mock" else None}
