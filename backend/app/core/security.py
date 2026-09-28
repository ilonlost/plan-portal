from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import httpx
from dataclasses import asdict, dataclass

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.entities import User


@dataclass(frozen=True)
class UserContext:
    username: str
    display_name: str
    role: str
    email: str = ""
    workshop_code: str | None = None
    line_name: str | None = None


LOCAL_USER_MARKER = "__local_development__"

SECTION_KEYS = ("plan", "catalog", "import", "az", "sources", "feedback", "fact")

def user_sections(user: User) -> dict[str, bool]:
    if user.role == "admin":
        return dict.fromkeys(SECTION_KEYS, True)
    defaults = {key: key != "fact" for key in SECTION_KEYS}
    return {**defaults, **(user.section_permissions or {})}

def check_section_access(request: Request, stored: User) -> None:
    """Enforce individual access on the server, including existing sessions."""
    path = request.url.path
    if not path.startswith("/api/"):
        path = "/api" + path
    sections = user_sections(stored)
    required = None
    for prefix, section in (("/api/plans", "plan"), ("/api/imports", "import"),
                            ("/api/advance-confirmations", "az"), ("/api/feedback", "feedback"),
                            ("/api/production-fact", "fact"), ("/api/integrations", "plan")):
        if path == prefix or path.startswith(prefix + "/"):
            required = section
            break
    if path.startswith("/api/catalog"):
        if path.endswith("/manual-products"):
            required = "plan"
        elif request.method == "GET" and (path == "/api/catalog" or path.endswith("/bom")):
            if not (sections["catalog"] or sections["sources"]):
                raise HTTPException(403, "Доступ к справочнику закрыт администратором")
        else:
            required = "catalog"
    if path in {"/api/dashboard", "/api/lines/insights"} or "/comments/" in path:
        required = "plan"
    elif path.startswith("/api/lines/") and request.method != "GET":
        required = "catalog"
    if path == "/api/admin/mail-preview":
        required = "plan"
    if required and not sections[required]:
        raise HTTPException(403, "Доступ к разделу закрыт администратором")


def configured_local_users() -> dict[str, tuple[UserContext, str]]:
    """Read development-only accounts from the ignored local environment."""
    if not settings.local_auth_enabled:
        return {}
    try:
        rows = json.loads(settings.local_auth_users_json)
    except json.JSONDecodeError:
        return {}
    users: dict[str, tuple[UserContext, str]] = {}
    if not isinstance(rows, list):
        return users
    for row in rows:
        if not isinstance(row, dict):
            continue
        username = str(row.get("username", "")).strip()
        password = str(row.get("password", ""))
        role = str(row.get("role", "viewer"))
        if not username or not password or role not in {"admin", "planner", "master", "viewer"}:
            continue
        users[username] = (
            UserContext(
                username=username,
                display_name=str(row.get("display_name") or username),
                role=role,
                email=str(row.get("email") or ""),
                workshop_code=row.get("workshop_code"),
                line_name=row.get("line_name"),
            ),
            password,
        )
    return users


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def create_session_token(user: UserContext) -> str:
    payload = {**asdict(user), "auth_method": settings.auth_mode, "exp": int(time.time()) + settings.session_max_age_seconds}
    encoded = _encode(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode())
    signature = _encode(hmac.new(settings.session_secret.encode(), encoded.encode(), hashlib.sha256).digest())
    return f"{encoded}.{signature}"


def parse_session_token(token: str | None) -> UserContext | None:
    if not token or "." not in token:
        return None


def art_portal_identity(request: Request) -> UserContext | None:
    """Ask ART to validate the browser's original session on every PLAN request.

    This keeps revocation server-side: a stale PLAN cookie by itself never grants
    access after ART disables the module or the user signs out.
    """
    if not settings.art_portal_sso_required:
        return None
    if not settings.art_portal_url or not settings.art_portal_integration_token:
        raise HTTPException(503, "Единый вход PLAN PORTAL не настроен")
    cookie = request.headers.get("cookie", "")
    try:
        response = httpx.get(
            f"{settings.art_portal_url.rstrip('/')}/api/integrations/plan/session",
            headers={"x-portal-integration-key": settings.art_portal_integration_token, "cookie": cookie},
            timeout=4.0,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(503, "ART PORTAL временно недоступен; доступ к планированию не подтверждён") from exc
    if response.status_code in {401, 403}:
        raise HTTPException(401, "Сессия ART PORTAL завершена или доступ к планированию отозван")
    if response.status_code != 200:
        raise HTTPException(503, "Не удалось подтвердить доступ к планированию")
    body = response.json().get("user") or {}
    try:
        return UserContext(str(body["username"]), str(body["display_name"]), str(body["role"]), str(body.get("email") or ""))
    except KeyError as exc:
        raise HTTPException(503, "ART PORTAL вернул неполный профиль единого входа") from exc
    encoded, signature = token.rsplit(".", 1)
    expected = _encode(hmac.new(settings.session_secret.encode(), encoded.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(_decode(encoded))
        if not settings.local_auth_enabled and payload.get("auth_method") != "ldap":
            return None
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return UserContext(
            username=payload["username"], display_name=payload["display_name"], role=payload["role"],
            email=payload.get("email", ""), workshop_code=payload.get("workshop_code"), line_name=payload.get("line_name"),
        )
    except (ValueError, KeyError, json.JSONDecodeError):
        return None


def current_user(
    request: Request, db: Session = Depends(get_db),
) -> UserContext:
    user = parse_session_token(request.cookies.get(settings.session_cookie_name))
    if not user:
        raise HTTPException(401, "Требуется вход в систему")
    art_identity = art_portal_identity(request)
    if settings.art_portal_sso_required and not art_identity:
        raise HTTPException(401, "Требуется вход через ART PORTAL")
    if art_identity and art_identity.username.lower() != user.username.lower():
        raise HTTPException(401, "Сессии порталов относятся к разным пользователям")
    stored = db.scalar(select(User).where(User.username == user.username))
    if stored:
        if art_identity:
            stored.display_name = art_identity.display_name
            stored.email = art_identity.email or stored.email
            stored.role = art_identity.role
            stored.active = True
            db.commit()
        if not stored.active:
            raise HTTPException(403, "Учётная запись отключена администратором")
        check_section_access(request, stored)
        return UserContext(
            stored.username, stored.display_name, stored.role, stored.email or user.email,
            stored.workshop_code or user.workshop_code, stored.line_name or user.line_name,
        )
    raise HTTPException(401, "Учётная запись сессии не найдена")


def require_planner(user: UserContext = Depends(current_user)) -> UserContext:
    if user.role not in {"admin", "planner"}:
        raise HTTPException(403, "Только планер или администратор может изменять план")
    return user


def require_admin(user: UserContext = Depends(current_user)) -> UserContext:
    if user.role != "admin":
        raise HTTPException(403, "Действие доступно только администратору")
    return user


def ensure_master_line(user: UserContext, workshop_code: str | None, line_name: str | None) -> None:
    if user.role in {"admin", "planner"}:
        return
    if user.role != "master" or user.workshop_code != workshop_code or user.line_name != line_name:
        raise HTTPException(403, "Мастер может менять статус только на закреплённой за ним линии")
