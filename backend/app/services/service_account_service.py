"""Production read-only identities; independent of development-only local users."""
import hashlib
import hmac
import secrets
import re

from fastapi import HTTPException
from sqlalchemy import func, select
from app.models.entities import User

SERVICE_SECTIONS = {"plan", "catalog", "fact"}


def password_hash(password: str) -> str:
    if not 12 <= len(password) <= 256:
        raise ValueError("Пароль должен содержать от 12 до 256 символов")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 600_000).hex()
    return f"pbkdf2_sha256$600000${salt}${digest}"


def verify_password(password: str, encoded: str | None) -> bool:
    try:
        algorithm, rounds, salt, expected = (encoded or "").split("$")
        if algorithm != "pbkdf2_sha256" or int(rounds) != 600_000:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(rounds)).hex()
        return hmac.compare_digest(digest, expected)
    except (ValueError, TypeError):
        return False


def create_service_account(db, username, display_name, password):
    username = username.strip().lower()
    if not re.fullmatch(r"svc\.[a-z0-9][a-z0-9._-]{2,59}", username):
        raise HTTPException(422, "Логин: svc. и от 3 до 60 латинских букв, цифр, точек, дефисов или подчёркиваний")
    if db.scalar(select(User.id).where(func.lower(User.username) == username)):
        raise HTTPException(409, "Такой логин уже занят")
    try:
        encoded = password_hash(password)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    row = User(username=username, display_name=display_name.strip() or username, role="viewer", active=True,
               auth_provider="service", password_hash=encoded, session_version=0,
               section_permissions={key: key in {"plan", "catalog"} for key in ("plan", "catalog", "fact", "import", "az", "feedback", "sources")})
    db.add(row)
    db.flush()
    return row
