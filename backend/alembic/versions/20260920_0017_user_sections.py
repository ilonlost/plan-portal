"""Per-user section access; preserve existing users and production data."""
from alembic import op
import sqlalchemy as sa

revision = "20260920_0017"
down_revision = "20260920_0016"
branch_labels = None
depends_on = None

def upgrade():
    op.add_column("users", sa.Column("section_permissions", sa.JSON(), nullable=False, server_default="{}"))
    # Preserve the former visibility for existing non-admin users, then make
    # each copy independent. Production fact previously required admin.
    connection = op.get_bind()
    settings_table = sa.table("portal_settings", sa.column("key", sa.String), sa.column("value", sa.JSON))
    saved = connection.execute(sa.select(settings_table.c.value).where(settings_table.c.key == "portal_configuration")).scalar() or {}
    permissions = dict(saved.get("section_visibility", {}))
    permissions["fact"] = False
    users = sa.table("users", sa.column("role", sa.String), sa.column("section_permissions", sa.JSON))
    connection.execute(users.update().where(users.c.role != "admin").values(section_permissions=permissions))

def downgrade():
    op.drop_column("users", "section_permissions")
