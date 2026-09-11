"""Database Reset and Clear Utility for Development & Testing Environments.

Provides a safe, fast mechanism to wipe PostgreSQL tables and re-initialize schemas.
Includes strict production safety guards to prevent accidental wipes.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Add project root to sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from sqlalchemy import text  # noqa: E402

from src.adapters.db.session import async_session_factory, close_db, engine, init_db, run_migrations  # noqa: E402
from src.config import settings  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("clear_db")


def check_production_safety_guard(guild_id: int | None = None) -> None:
    """Blocks execution in production or if connected to a remote production database."""
    env = (settings.ENVIRONMENT or "").lower()
    if env in ("prod", "production"):
        raise RuntimeError(f"CRITICAL SAFETY VIOLATION: Database clear cannot be run in '{env}' environment!")

    db_url = (settings.DATABASE_URL or "").lower()
    unsafe_keywords = [
        "rds.amazonaws.com",
        "supabase.co",
        "neon.tech",
        "cockroachlabs.cloud",
        "render.com",
        "railway.app",
        "cloudsql",
    ]
    if any(kw in db_url for kw in unsafe_keywords):
        raise RuntimeError(
            f"CRITICAL SAFETY VIOLATION: DATABASE_URL appears to point to a production/cloud database ({db_url})! "
            "Clear aborted to prevent data loss."
        )


async def clear_database(guild_id: int | None = None) -> None:
    """Purges all records from PostgreSQL database tables.

    If guild_id is provided, only records associated with that guild are deleted.
    Otherwise, all tables are dropped and re-created cleanly with the latest schema.
    """
    check_production_safety_guard(guild_id)

    logger.info("=" * 60)
    if guild_id:
        logger.info(f"🧹 Clearing database records for Discord Guild ID: {guild_id}...")
        await init_db()
        async with async_session_factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "DELETE FROM task_dependencies WHERE task_id IN (SELECT id FROM tasks WHERE guild_id = :gid) "
                        "OR depends_on_task_id IN (SELECT id FROM tasks WHERE guild_id = :gid)"
                    ),
                    {"gid": guild_id},
                )
                await session.execute(
                    text("DELETE FROM task_watchers WHERE task_id IN (SELECT id FROM tasks WHERE guild_id = :gid)"),
                    {"gid": guild_id},
                )
                await session.execute(
                    text("DELETE FROM task_history WHERE task_id IN (SELECT id FROM tasks WHERE guild_id = :gid)"),
                    {"gid": guild_id},
                )
                await session.execute(
                    text("DELETE FROM tasks WHERE guild_id = :gid"),
                    {"gid": guild_id},
                )
                await session.execute(
                    text(
                        "DELETE FROM project_squads WHERE project_id IN (SELECT id FROM projects WHERE guild_id = :gid)"
                    ),
                    {"gid": guild_id},
                )
                await session.execute(
                    text("DELETE FROM projects WHERE guild_id = :gid"),
                    {"gid": guild_id},
                )
                await session.execute(
                    text("DELETE FROM squad_members WHERE squad_id IN (SELECT id FROM squads WHERE guild_id = :gid)"),
                    {"gid": guild_id},
                )
                await session.execute(
                    text("DELETE FROM squads WHERE guild_id = :gid"),
                    {"gid": guild_id},
                )
                await session.execute(
                    text("DELETE FROM user_preferences WHERE guild_id = :gid"),
                    {"gid": guild_id},
                )
                await session.execute(
                    text("DELETE FROM outbox_events"),
                )
    else:
        logger.info("🧹 Dropping and re-creating ALL database tables fresh via Alembic...")
        # Note: On PostgreSQL 15+, `CREATE SCHEMA public` no longer grants CREATE privileges to
        # PUBLIC by default (CVE-2018-1058 defense). This script assumes the connecting database
        # user (e.g., 'postgres' in local development and Docker) owns the database and the public
        # schema with full DDL privileges. If executing in hosted environments with non-owner roles,
        # ensure proper schema ownership or CREATE permissions are granted.
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
        await run_migrations()

    logger.info("=" * 60)
    await close_db()
    logger.info("✔ PostgreSQL database successfully cleared and reset!")


def main() -> None:
    parser = argparse.ArgumentParser(description="Clear and reset the dgg-pm PostgreSQL database.")
    parser.add_argument(
        "--guild-id",
        type=int,
        default=None,
        help="Optional target Discord Guild ID (if omitted, clears all local database records)",
    )
    parser.add_argument(
        "--seed",
        action="store_true",
        help="Immediately re-seed test data after clearing the database",
    )
    args = parser.parse_args()

    asyncio.run(clear_database(args.guild_id))

    if args.seed:
        from scripts.seed_dev_data import seed_data

        target_guild = args.guild_id or settings.DISCORD_GUILD_ID or 1543430283250901023
        logger.info(f"🌱 Re-seeding test data for Guild {target_guild}...")
        asyncio.run(seed_data(target_guild, reset=False))


if __name__ == "__main__":
    main()
