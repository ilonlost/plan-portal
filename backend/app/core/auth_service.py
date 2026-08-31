from __future__ import annotations

from dataclasses import dataclass
import ssl

from ldap3 import ALL, SUBTREE, Connection, Server, Tls
from ldap3.core.exceptions import LDAPBindError, LDAPException
from ldap3.utils.conv import escape_filter_chars

from app.core.config import settings


@dataclass(frozen=True)
class AuthenticatedIdentity:
    username: str
    display_name: str
    email: str
    role: str
    groups: list[str]


def _server() -> Server:
    url = settings.ldap_server_url.strip()
    host = settings.ldap_server.strip()
    port = settings.ldap_port
    use_ssl = settings.ldap_use_ssl
    if url:
        clean = url.removeprefix("ldap://").removeprefix("ldaps://").rstrip("/")
        if ":" in clean:
            host, raw_port = clean.rsplit(":", 1)
            port = int(raw_port)
        else:
            host = clean
        use_ssl = url.startswith("ldaps://")
    if not host:
        raise ValueError("LDAP_SERVER_URL не настроен")
    tls = Tls(
        validate=ssl.CERT_REQUIRED if settings.ldap_tls_validate else ssl.CERT_NONE,
        ca_certs_file=settings.ldap_ca_file or None,
    )
    return Server(host, port=port, use_ssl=use_ssl, tls=tls, get_info=ALL, connect_timeout=settings.ldap_timeout)


def _bind_identity(username: str) -> str:
    template = settings.ldap_bind_format.strip()
    if template:
        return template.replace("{username}", username).replace("{domain}", settings.ldap_domain)
    if "@" in username or "\\" in username:
        return username
    login_format = settings.ldap_login_format.strip().lower()
    if login_format == "dn":
        return username
    if login_format == "netbios":
        domain = settings.ldap_netbios_domain or settings.ldap_domain
        return f"{domain}\\{username}" if domain else username
    if login_format in {"userprincipalname", "upn"}:
        suffix = settings.ldap_upn_suffix or settings.ldap_domain
        return f"{username}@{suffix}" if suffix else username
    if settings.ldap_upn_suffix:
        return f"{username}@{settings.ldap_upn_suffix}"
    if settings.ldap_domain:
        return f"{settings.ldap_domain}\\{username}"
    return username


def _requested_attributes() -> list[str]:
    configured = [value.strip() for value in settings.ldap_user_attributes.split(",") if value.strip()]
    required = ["sAMAccountName", "displayName", "mail", "memberOf", "cn"]
    return list(dict.fromkeys([*configured, *required]))


def _connect(user: str, password: str) -> Connection:
    connection = Connection(_server(), user=user, password=password, raise_exceptions=True, receive_timeout=settings.ldap_timeout)
    connection.open()
    if settings.ldap_start_tls:
        connection.start_tls()
    connection.bind()
    return connection


def _role(username: str, groups: list[str]) -> str:
    normalized = {value.lower() for value in groups}
    admins = {value.strip().lower() for value in settings.portal_admin_logins.split(",") if value.strip()}
    if username.lower() in admins or bool(settings.ldap_admin_group_dn and settings.ldap_admin_group_dn.lower() in normalized):
        return "admin"
    # Из окружения назначаются только первоначальные администраторы. Остальные
    # права хранятся в БД и управляются через веб-админку.
    return "viewer"


def authenticate_ldap(username: str, password: str) -> AuthenticatedIdentity:
    clean = username.strip()
    if not clean or not password:
        raise ValueError("Введите логин и пароль")
    try:
        user_connection = _connect(_bind_identity(clean), password)
    except LDAPBindError as exc:
        raise ValueError("Неверный логин или пароль") from exc
    except (LDAPException, OSError) as exc:
        raise ValueError("Сервис LDAP временно недоступен") from exc

    search_connection = user_connection
    try:
        if settings.ldap_bind_dn and settings.ldap_bind_password:
            search_connection = _connect(settings.ldap_bind_dn, settings.ldap_bind_password)
        filter_value = (settings.ldap_user_filter or "(sAMAccountName={username})").replace(
            "{username}", escape_filter_chars(clean.split("@")[0].split("\\")[-1]),
        )
        found = search_connection.search(
            settings.ldap_base_dn, filter_value, search_scope=SUBTREE,
            attributes=_requested_attributes(), size_limit=1,
        )
        if not found or not search_connection.entries:
            raise ValueError("Пользователь не найден в LDAP")
        entry = search_connection.entries[0]
        account = str(getattr(entry, "sAMAccountName", clean) or clean)
        display = str(getattr(entry, "displayName", "") or getattr(entry, "cn", "") or account)
        email = str(getattr(entry, "mail", "") or "")
        raw_groups = getattr(entry, "memberOf", None)
        groups = [str(value) for value in (raw_groups.values if raw_groups else [])]
        return AuthenticatedIdentity(account, display, email, _role(account, groups), groups)
    finally:
        if search_connection is not user_connection:
            search_connection.unbind()
        user_connection.unbind()


def ldap_health() -> dict:
    if settings.auth_mode.lower() != "ldap":
        return {"status": "mock", "configured": False}
    configured = bool((settings.ldap_server_url or settings.ldap_server) and settings.ldap_base_dn)
    return {"status": "configured" if configured else "not_configured", "configured": configured}
