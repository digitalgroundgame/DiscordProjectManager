"""add_task_start_at

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-13 00:34:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema to add start_at column and index to tasks table."""
    op.add_column("tasks", sa.Column("start_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(op.f("ix_tasks_start_at"), "tasks", ["start_at"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_tasks_start_at"), table_name="tasks")
    op.drop_column("tasks", "start_at")
