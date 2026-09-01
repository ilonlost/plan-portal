"""enterprise auth, audit, notifications, integrations and merged KC

Revision ID: 20260827_0004
Revises: 20260827_0003
"""
from alembic import op
import sqlalchemy as sa


revision = "20260827_0004"
down_revision = "20260827_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    user_columns = {item["name"] for item in inspector.get_columns("users")}
    for column in (
        sa.Column("email", sa.String(200)),
        sa.Column("workshop_code", sa.String(20)),
        sa.Column("line_name", sa.String(160)),
        sa.Column("ldap_groups", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
    ):
        if column.name not in user_columns:
            op.add_column("users", column)

    op.execute("UPDATE production_lines SET workshop_code='KC', workshop_name='КЦ' WHERE workshop_code IN ('KC1','KC2')")
    op.execute("UPDATE users SET workshop_code='KC' WHERE workshop_code IN ('KC1','KC2')")

    def has_index(table: str, name: str) -> bool:
        return any(value["name"] == name for value in sa.inspect(op.get_bind()).get_indexes(table))

    if not inspector.has_table("auth_audit_events"):
        op.create_table(
            "auth_audit_events",
            sa.Column("id", sa.Integer(), primary_key=True), sa.Column("username", sa.String(120), nullable=False),
            sa.Column("display_name", sa.String(200)), sa.Column("success", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("ip_address", sa.String(80)), sa.Column("user_agent", sa.String(500)),
            sa.Column("auth_method", sa.String(30), nullable=False, server_default="ldap"), sa.Column("failure_reason", sa.Text()),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
    if not has_index("auth_audit_events", "ix_auth_audit_events_username"):
        op.create_index("ix_auth_audit_events_username", "auth_audit_events", ["username"])

    if not inspector.has_table("audit_events"):
        op.create_table(
            "audit_events",
            sa.Column("id", sa.Integer(), primary_key=True), sa.Column("username", sa.String(120), nullable=False),
            sa.Column("action", sa.String(80), nullable=False), sa.Column("entity_type", sa.String(80), nullable=False),
            sa.Column("entity_id", sa.String(80)), sa.Column("details", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
    if not has_index("audit_events", "ix_audit_events_username"):
        op.create_index("ix_audit_events_username", "audit_events", ["username"])
    if not has_index("audit_events", "ix_audit_events_action"):
        op.create_index("ix_audit_events_action", "audit_events", ["action"])

    if not inspector.has_table("notification_logs"):
        op.create_table(
            "notification_logs",
            sa.Column("id", sa.Integer(), primary_key=True), sa.Column("event_type", sa.String(80), nullable=False),
            sa.Column("recipients", sa.JSON(), nullable=False, server_default="[]"), sa.Column("subject", sa.String(300), nullable=False),
            sa.Column("status", sa.String(30), nullable=False, server_default="pending"), sa.Column("message_id", sa.String(300)),
            sa.Column("error", sa.Text()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
    if not has_index("notification_logs", "ix_notification_logs_event_type"):
        op.create_index("ix_notification_logs_event_type", "notification_logs", ["event_type"])

    if not inspector.has_table("integration_runs"):
        op.create_table(
            "integration_runs",
            sa.Column("id", sa.Integer(), primary_key=True), sa.Column("integration", sa.String(40), nullable=False),
            sa.Column("operation", sa.String(80), nullable=False), sa.Column("target_date", sa.Date()),
            sa.Column("status", sa.String(30), nullable=False), sa.Column("test_mode", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"), sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("response", sa.JSON(), nullable=False, server_default="{}"), sa.Column("created_by", sa.String(120), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        )
    if not has_index("integration_runs", "ix_integration_runs_integration"):
        op.create_index("ix_integration_runs_integration", "integration_runs", ["integration"])


def downgrade() -> None:
    op.drop_table("integration_runs")
    op.drop_table("notification_logs")
    op.drop_table("audit_events")
    op.drop_table("auth_audit_events")
    for column in ("last_login_at", "ldap_groups", "line_name", "workshop_code", "email"):
        op.drop_column("users", column)
