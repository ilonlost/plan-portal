from app.core import auth_service
from app.core.auth_service import _bind_identity, _requested_attributes, search_ldap_users
from app.core.config import settings


def test_ldap_bind_identity_matches_art_portal_upn_format(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ldap_bind_format", "")
    monkeypatch.setattr(settings, "ldap_login_format", "userPrincipalName")
    monkeypatch.setattr(settings, "ldap_domain", "")
    monkeypatch.setattr(settings, "ldap_upn_suffix", "agrohold.ru")

    assert _bind_identity("planner.user") == "planner.user@agrohold.ru"


def test_ldap_bind_identity_supports_art_portal_netbios_and_template(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ldap_bind_format", "")
    monkeypatch.setattr(settings, "ldap_login_format", "netbios")
    monkeypatch.setattr(settings, "ldap_netbios_domain", "AGROHOLD")
    monkeypatch.setattr(settings, "ldap_domain", "")
    assert _bind_identity("planner.user") == "AGROHOLD\\planner.user"

    monkeypatch.setattr(settings, "ldap_bind_format", "{domain}\\{username}")
    monkeypatch.setattr(settings, "ldap_domain", "FK")
    assert _bind_identity("planner.user") == "FK\\planner.user"


def test_ldap_uses_shared_art_portal_attribute_configuration(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ldap_user_attributes", "userPrincipalName,displayName,distinguishedName")

    attributes = _requested_attributes()
    assert attributes[:3] == ["userPrincipalName", "displayName", "distinguishedName"]
    assert "memberOf" in attributes
    assert "mail" in attributes


def test_directory_search_uses_service_account_and_returns_safe_user_fields(monkeypatch) -> None:
    class Entry:
        sAMAccountName = "ivanov.i"
        displayName = "Иванов Иван"
        mail = "ivanov.i@agrohold.ru"
        department = "Планирование"
        title = "Планер"
        cn = "Иванов Иван"

    class Connection:
        entries = [Entry()]

        def __init__(self) -> None:
            self.query: tuple[object, ...] | None = None
            self.unbound = False

        def search(self, *args, **kwargs) -> bool:
            self.query = (*args, kwargs)
            return True

        def unbind(self) -> None:
            self.unbound = True

    connection = Connection()
    monkeypatch.setattr(settings, "auth_mode", "ldap")
    monkeypatch.setattr(settings, "ldap_base_dn", "DC=agrohold,DC=ru")
    monkeypatch.setattr(settings, "ldap_bind_dn", "CN=plan-portal,OU=Services,DC=agrohold,DC=ru")
    monkeypatch.setattr(settings, "ldap_bind_password", "secret")
    monkeypatch.setattr(settings, "ldap_search_limit", 12)
    monkeypatch.setattr(auth_service, "_connect", lambda user, password: connection)

    users = search_ldap_users("иван*")

    assert users[0].username == "ivanov.i"
    assert users[0].display_name == "Иванов Иван"
    assert users[0].department == "Планирование"
    assert connection.query is not None
    assert "\\2a" in str(connection.query)
    assert connection.unbound is True


def test_directory_search_requires_ldap_service_account(monkeypatch) -> None:
    monkeypatch.setattr(settings, "auth_mode", "ldap")
    monkeypatch.setattr(settings, "ldap_base_dn", "DC=agrohold,DC=ru")
    monkeypatch.setattr(settings, "ldap_bind_dn", "")
    monkeypatch.setattr(settings, "ldap_bind_password", "")

    try:
        search_ldap_users("иван")
    except ValueError as exc:
        assert "LDAP_BIND_DN" in str(exc)
    else:
        raise AssertionError("Expected LDAP service-account validation error")
