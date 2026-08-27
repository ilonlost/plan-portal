from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import asdict, dataclass

from fastapi import Depends, Header, HTTPException, Request
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


DEMO_USERS = {
    "demo.admin": UserContext("demo.admin", "Локальный администратор", "admin", "admin@localhost"),
    "demo.planner": UserContext("demo.planner", "Анна · планер", "planner", "planner@localhost"),
    "master.sandwich": UserContext("master.sandwich", "Иван · мастер сэндвичей", "master", "master@localhost", "KC", "Сэндвичи"),
    "master.sloyka": UserContext("master.sloyka", "Ольга · мастер слойки", "master", "master@localhost", "PC", "Слойка"),
    "viewer": UserContext("viewer", "Просмотр", "viewer"),
}


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def create_session_token(user: UserContext) -> str:
    payload = {**asdict(user), "exp": int(time.time()) + settings.session_max_age_seconds}
    encoded = _encode(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode())
    signature = _encode(hmac.new(settings.session_secret.encode(), encoded.encode(), hashlib.sha256).digest())
    return f"{encoded}.{signature}"


def parse_session_token(token: str | None) -> UserContext | None:
    if not token or "." not in token:
        return None
    encoded, signature = token.rsplit(".", 1)
    expected = _encode(hmac.new(settings.session_secret.encode(), encoded.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(_decode(encoded))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        return UserContext(
            username=payload["username"], display_name=payload["display_name"], role=payload["role"],
            email=payload.get("email", ""), workshop_code=payload.get("workshop_code"), line_name=payload.get("line_name"),
        )
    except (ValueError, KeyError, json.JSONDecodeError):
        return None


def current_user(
    request: Request, x_user: str | None = Header(default=None, alias="X-User"), db: Session = Depends(get_db),
) -> UserContext:
    user = parse_session_token(request.cookies.get(settings.session_cookie_name))
    if not user and settings.auth_mode.lower() == "mock" and x_user:
        user = DEMO_USERS.get(x_user)
    if not user:
        raise HTTPException(401, "Требуется вход в систему")
    stored = db.scalar(select(User).where(User.username == user.username))
    if stored:
        if not stored.active:
            raise HTTPException(403, "Учётная запись отключена администратором")
        return UserContext(
            stored.username, stored.display_name, stored.role, stored.email or user.email,
            stored.workshop_code or user.workshop_code, stored.line_name or user.line_name,
        )
    return user


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
