from app.core.auth_service import _bind_identity, _requested_attributes
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
