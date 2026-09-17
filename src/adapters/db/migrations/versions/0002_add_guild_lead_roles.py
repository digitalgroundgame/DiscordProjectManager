"""add_guild_lead_roles

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-17 18:12:06.514224

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
    """Upgrade schema."""
    op.create_table(
        "guild_lead_roles",
        sa.Column("guild_id", sa.BigInteger(), nullable=False),
        sa.Column("discord_role_id", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("guild_id", "discord_role_id"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("guild_lead_roles")
