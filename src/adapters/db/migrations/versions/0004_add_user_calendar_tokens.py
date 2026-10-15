"""add_user_calendar_tokens

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-14 19:35:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "user_calendar_tokens",
        sa.Column("user_discord_id", sa.BigInteger(), nullable=False),
        sa.Column("token", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_discord_id"),
    )
    op.create_index(op.f("ix_user_calendar_tokens_token"), "user_calendar_tokens", ["token"], unique=True)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_user_calendar_tokens_token"), table_name="user_calendar_tokens")
    op.drop_table("user_calendar_tokens")
