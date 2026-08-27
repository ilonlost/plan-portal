from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Production Planning Portal"
    database_url: str = "sqlite:///./planning.sqlite3"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    app_url: str = "http://127.0.0.1:18095"
    auth_mode: str = "mock"
    session_secret: str = "local-development-secret-change-in-production"
    session_cookie_name: str = "plan_portal_session"
    session_max_age_seconds: int = 28800
    session_cookie_secure: bool = False
    session_cookie_samesite: str = "Lax"
    mock_password: str = "demo"
    ldap_server_url: str = ""
    ldap_server: str = ""
    ldap_port: int = 389
    ldap_use_ssl: bool = False
    ldap_start_tls: bool = False
    ldap_timeout: int = 10
    ldap_ca_file: str = ""
    ldap_tls_validate: bool = True
    ldap_base_dn: str = ""
    ldap_bind_dn: str = ""
    ldap_bind_password: str = ""
    ldap_user_filter: str = "(sAMAccountName={username})"
    ldap_domain: str = ""
    ldap_upn_suffix: str = ""
    ldap_admin_group_dn: str = ""
    ldap_planner_group_dn: str = ""
    ldap_master_group_dn: str = ""
    portal_admin_logins: str = ""
    email_enabled: bool = False
    smtp_host: str = "smtp.agrohold.ru"
    smtp_port: int = 25
    smtp_from: str = "planfkportal@agrohold.ru"
    smtp_from_name: str = "PLAN PORTAL"
    smtp_reply_to: str = ""
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_secure: bool = False
    smtp_require_tls: bool = True
    smtp_timeout_ms: int = 10000
    smtp_ca_file: str = ""
    smtp_tls_validate: bool = True
    notification_emails: str = ""
    csb_test_mode: bool = True
    csb_endpoint: str = ""
    csb_token: str = ""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [value.strip() for value in self.cors_origins.split(",") if value.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
