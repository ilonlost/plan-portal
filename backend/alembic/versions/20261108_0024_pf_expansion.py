"""Link expanded PF tasks and preserve their stage speeds."""
from alembic import op
import sqlalchemy as sa

revision = "20261108_0024"
down_revision = "20261023_0023"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("production_schedule_items") as batch:
        batch.add_column(sa.Column("parent_task_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("pf_details", sa.JSON(), nullable=True))
        batch.create_foreign_key("fk_schedule_parent_task", "production_schedule_items", ["parent_task_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_production_schedule_items_parent_task_id", ["parent_task_id"])


def downgrade():
    with op.batch_alter_table("production_schedule_items") as batch:
        batch.drop_index("ix_production_schedule_items_parent_task_id")
        batch.drop_constraint("fk_schedule_parent_task", type_="foreignkey")
        batch.drop_column("pf_details")
        batch.drop_column("parent_task_id")
