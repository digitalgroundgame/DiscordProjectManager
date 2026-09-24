"""add_guild_lead_users

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-24 02:17:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "guild_lead_users",
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("user_discord_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("guild_id", "user_discord_id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("guild_lead_users")
