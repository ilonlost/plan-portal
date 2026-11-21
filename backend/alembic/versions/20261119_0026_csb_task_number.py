"""Keep a permanent five-digit CSB number for each exported task."""
from alembic import op
import sqlalchemy as sa

revision = "20261119_0026"
down_revision = "20261119_0025"
branch_labels = None
depends_on = None


def upgrade():
    schema = sa.inspect(op.get_bind())
    if not schema.has_table("csb_task_counter"):
        op.create_table(
            "csb_task_counter",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
            sa.Column("last_number", sa.Integer(), nullable=False),
        )
    table = "production_schedule_items"
    if "csb_task_number" not in {column["name"] for column in schema.get_columns(table)}:
        op.add_column(table, sa.Column("csb_task_number", sa.Integer(), sa.CheckConstraint("csb_task_number BETWEEN 1 AND 99999")))
    index = "ix_production_schedule_items_csb_task_number"
    if index not in {entry["name"] for entry in schema.get_indexes(table)}:
        op.create_index(index, table, ["csb_task_number"], unique=True)


def downgrade():
    op.drop_index("ix_production_schedule_items_csb_task_number", table_name="production_schedule_items")
    op.drop_column("production_schedule_items", "csb_task_number")
    op.drop_table("csb_task_counter")
