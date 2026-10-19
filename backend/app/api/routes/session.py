from datetime import datetime, timezone, timedelta
import hmac
import httpx

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.core.auth_service import authenticate_ldap
from app.core.config import settings
from app.core.security import LOCAL_USER_MARKER, UserContext, configured_local_users, create_session_token, current_user, parse_art_portal_handoff, user_sections
from app.db.session import get_db
from app.models.entities import AuthAuditEvent, User


router = APIRouter(prefix="/session", tags=["session"])


class LoginRequest(BaseModel):
    username: str
    password: str


class ArtPortalHandoff(BaseModel):
    handoff_token: str


def _integration_authorized(request: Request) -> bool:
    expected = settings.art_portal_integration_token
    received = request.headers.get("x-portal-integration-key", "")
    return bool(expected and len(expected) == len(received) and hmac.compare_digest(expected, received))


def _upsert_art_user(identity: UserContext, db: Session) -> UserContext:
    stored = db.scalar(select(User).where(func.lower(User.username) == identity.username.casefold()))
    if stored and stored.auth_provider == "service":
        raise HTTPException(403, "Сервисная учётная запись не используется для входа через AD")
    if not stored:
        stored = User(username=identity.username, display_name=identity.display_name, email=identity.email or None, role=identity.role, active=True, ldap_groups=[])
        db.add(stored)
    else:
        stored.display_name, stored.email, stored.role, stored.active = identity.display_name, identity.email or stored.email, identity.role, True
    stored.last_login_at = datetime.now(timezone.utc)
    db.add(AuthAuditEvent(username=identity.username, display_name=identity.display_name, success=True, ip_address=None, user_agent="ART PORTAL SSO", auth_method="art_portal_sso"))
    db.commit()
    return UserContext(stored.username, stored.display_name, stored.role, stored.email or "", stored.workshop_code, stored.line_name)


def _user_dict(user: UserContext, db: Session | None = None) -> dict:
    result = {
        "username": user.username, "display_name": user.display_name, "role": user.role, "email": user.email,
        "workshop_code": user.workshop_code, "line_name": user.line_name,
        "access_label": {"admin": "Администратор", "planner": "Планирование", "master": "Мастер линии", "viewer": "Просмотр"}.get(user.role, user.role),
    }
    if db is not None:
        stored = db.scalar(select(User).where(User.username == user.username))
        result["section_visibility"] = user_sections(stored) if stored else {}
    return result


@router.post("/login")
def login(payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    if settings.art_portal_sso_required:
        raise HTTPException(403, "Вход выполняется через ART PORTAL")
    login_name = payload.username.strip()
    if db.scalar(select(User.id).where(func.lower(User.username) == login_name.casefold(), User.auth_provider == "service")):
        raise HTTPException(401, "Используйте вход для сервисной учётной записи")
    try:
        if settings.auth_mode.lower() == "ldap":
            identity = authenticate_ldap(login_name, payload.password)
            context = UserContext(identity.username, identity.display_name, identity.role, identity.email)
            groups = identity.groups
        elif settings.local_auth_enabled:
            local_entry = configured_local_users().get(login_name)
            if not local_entry or not hmac.compare_digest(payload.password, local_entry[1]):
                raise ValueError("Неверный логин или пароль")
            context = local_entry[0]
            groups = [LOCAL_USER_MARKER]
        else:
            raise ValueError("Локальная авторизация отключена")
        stored = db.scalar(select(User).where(User.username == context.username))
        if not stored:
            stored = User(
                username=context.username, display_name=context.display_name, role=context.role, email=context.email,
                workshop_code=context.workshop_code, line_name=context.line_name, ldap_groups=groups,
            )
            db.add(stored)
        else:
            if not stored.active:
                raise ValueError("Учётная запись отключена администратором")
            stored.display_name = context.display_name
            stored.email = context.email or stored.email
            stored.ldap_groups = groups
            if settings.auth_mode.lower() == "local":
                stored.role, stored.workshop_code, stored.line_name = context.role, context.workshop_code, context.line_name
            elif context.role == "admin":
                # Администраторы из ENV / группы AD сохраняют аварийный доступ
                # к админке и не могут быть случайно лишены его через веб-интерфейс.
                stored.role, stored.workshop_code, stored.line_name = "admin", None, None
                stored.active = True
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
        return {"user": _user_dict(resolved, db), "auth_mode": settings.auth_mode.lower()}
    except ValueError as exc:
        db.add(AuthAuditEvent(
            username=login_name, success=False, ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"), auth_method=settings.auth_mode.lower(), failure_reason=str(exc),
        ))
        db.commit()
        raise HTTPException(401, str(exc)) from exc


@router.post("/service-login")
def service_login(payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    from app.services.service_account_service import verify_password
    login_name = payload.username.strip().casefold()[:80]
    since = datetime.now(timezone.utc) - timedelta(minutes=15)
    attempts = db.scalar(select(func.count()).select_from(AuthAuditEvent).where(
        AuthAuditEvent.auth_method == "service", AuthAuditEvent.username == login_name,
        AuthAuditEvent.success.is_(False), AuthAuditEvent.created_at >= since,
    )) or 0
    if attempts >= 8:
        raise HTTPException(429, "Слишком много попыток. Повторите вход через 15 минут")
    stored = db.scalar(select(User).where(func.lower(User.username) == login_name, User.auth_provider == "service"))
    valid = len(payload.password) <= 256 and verify_password(payload.password, stored.password_hash if stored else "pbkdf2_sha256$600000$invalid$invalid")
    success = bool(valid and stored and stored.active and stored.role == "viewer")
    db.add(AuthAuditEvent(username=login_name, success=success, auth_method="service",
        ip_address=request.client.host if request.client else None, user_agent=request.headers.get("user-agent"),
        failure_reason=None if success else "Неверный логин или пароль"))
    if not success:
        db.commit()
        raise HTTPException(401, "Неверный логин или пароль")
    stored.last_login_at = datetime.now(timezone.utc)
    context = UserContext(stored.username, stored.display_name, "viewer", stored.email or "", auth_method="service", session_version=stored.session_version)
    db.commit()
    response.set_cookie(settings.session_cookie_name, create_session_token(context), max_age=settings.session_max_age_seconds,
        httponly=True, secure=settings.session_cookie_secure, samesite=settings.session_cookie_samesite.lower(), path="/")
    return {"user": _user_dict(context, db), "auth_mode": "service"}


@router.post("/art-portal")
def exchange_art_portal_session(payload: ArtPortalHandoff, response: Response, db: Session = Depends(get_db)) -> dict:
    """Create a PLAN cookie from ART's short-lived signed browser handoff."""
    if not settings.art_portal_sso_required:
        raise HTTPException(404, "Единый вход не включён")
    if not settings.art_portal_url or not settings.art_portal_integration_token:
        raise HTTPException(503, "Единый вход PLAN PORTAL не настроен")
    identity = parse_art_portal_handoff(payload.handoff_token)
    if not identity:
        raise HTTPException(401, "Войдите в ART PORTAL и повторите переход в планирование")
    user = _upsert_art_user(identity, db)
    response.set_cookie(settings.session_cookie_name, create_session_token(user), max_age=settings.session_max_age_seconds, httponly=True, secure=settings.session_cookie_secure, samesite=settings.session_cookie_samesite.lower(), path="/")
    return {"user": _user_dict(user, db), "auth_mode": "art_portal_sso"}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(settings.session_cookie_name, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: UserContext = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return {**_user_dict(user, db), "auth_mode": settings.auth_mode.lower()}


@router.get("/mode")
def mode() -> dict:
    if settings.art_portal_sso_required:
        return {"auth_mode": "art_portal_sso"}
    return {"auth_mode": "local" if settings.local_auth_enabled else "ldap"}
