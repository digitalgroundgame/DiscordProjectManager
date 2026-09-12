"""Unified Declarative Seeding Runner for dgg-pm.

Loads YAML seed manifests from `seeds/base/` and optional `seeds/profiles/`,
safely resets dev server state, and provisions both PostgreSQL records and Discord
workspaces (categories, forum channels, tags, pinned control hubs, and thread cards).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

# Ensure project root is in sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import discord  # noqa: E402

from src.adapters.db.postgres_repo import (  # noqa: E402
    PostgresOutboxRepo,
    PostgresProjectRepo,
    PostgresSquadRepo,
    PostgresTaskRepo,
    PostgresUserPreferenceRepo,
)
from src.adapters.db.session import async_session_factory, close_db, init_db  # noqa: E402
from src.adapters.db.unit_of_work import SqlAlchemyUnitOfWork  # noqa: E402
from src.config import settings  # noqa: E402
from src.services.outbox_service import OutboxService  # noqa: E402
from src.services.project_service import ProjectService  # noqa: E402
from src.services.seed.discord_provisioner import (  # noqa: E402
    DEFAULT_MANAGED_CATEGORY,
    clean_managed_category_channels,
    provision_discord_workspaces,
    resolve_and_provision_guild_roles,
)
from src.services.seed.manifest_loader import load_seed_manifest  # noqa: E402
from src.services.seed.seed_service import SeedDbResult, seed_database  # noqa: E402
from src.services.squad_service import SquadService  # noqa: E402
from src.services.task_service import TaskService  # noqa: E402
from src.services.user_service import UserService  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("seed")

DEFAULT_SEEDS_DIR = _PROJECT_ROOT / "seeds"


def check_production_safety_guard(guild_id: int | None = None) -> None:
    """Blocks execution in production or if connected to a remote production database."""
    env = (settings.ENVIRONMENT or "").lower().strip()
    if env in ("prod", "production", "live"):
        raise RuntimeError(f"⛔ SAFETY BLOCK: Seeding script cannot be run in production (ENVIRONMENT='{env}').")

    db_url = (settings.DATABASE_URL or "").lower()
    unsafe_keywords = [
        "rds.amazonaws.com",
        "supabase.co",
        "supabase.com",
        "neon.tech",
        "cockroachlabs.cloud",
        "render.com",
        "railway.app",
        "cloudsql",
        "elephantsql.com",
    ]
    if any(kw in db_url for kw in unsafe_keywords):
        raise RuntimeError(
            f"⛔ SAFETY BLOCK: DATABASE_URL appears to point to a production/cloud database ({db_url})! "
            "Seeding aborted to prevent accidental data loss."
        )


async def run_seed(
    seeds_dir: Path | str = DEFAULT_SEEDS_DIR,
    guild_id: int | None = None,
    profile_name: str | None = None,
    no_discord: bool = False,
    reset: bool = True,
    session_factory: Any | None = None,
) -> SeedDbResult:
    check_production_safety_guard(guild_id)

    target_guild_id = guild_id or settings.DISCORD_GUILD_ID
    if not target_guild_id:
        raise ValueError("No target Guild ID specified and DISCORD_GUILD_ID is not configured.")

    sess_factory = session_factory or async_session_factory

    logger.info("=" * 60)
    logger.info("🌱 Declarative Seed Runner")
    logger.info("   • Target Guild ID: %s", target_guild_id)
    logger.info("   • Seeds Directory: %s", seeds_dir)
    logger.info("   • Profile Overlay: %s", profile_name or "None (Base only)")
    logger.info("   • Reset Mode     : %s", "Destructive Wipe" if reset else "Preserve/Upsert")
    logger.info("=" * 60)

    # 1. Load and validate manifest
    manifest = load_seed_manifest(seeds_dir, profile_name=profile_name)
    logger.info(
        "Loaded manifest: %d squads, %d projects, %d channels, %d tasks",
        len(manifest.squads),
        len(manifest.projects),
        len(manifest.channels),
        len(manifest.tasks),
    )

    discord_client: discord.Client | None = None
    discord_guild: discord.Guild | None = None
    role_mapping: dict[str, int] = {}

    # 2. Discord connection and role resolution (if enabled)
    use_discord = not no_discord and bool(settings.DISCORD_BOT_TOKEN)
    if use_discord:
        logger.info("Connecting to Discord client...")
        discord_client = discord.Client(intents=discord.Intents.default())
        await discord_client.login(settings.DISCORD_BOT_TOKEN)
        try:
            discord_guild = await discord_client.fetch_guild(target_guild_id)
            logger.info("Authenticated with Guild: '%s'", discord_guild.name)

            if reset:
                logger.info("Cleaning Discord channels within managed category '%s'...", DEFAULT_MANAGED_CATEGORY)
                await clean_managed_category_channels(discord_guild, DEFAULT_MANAGED_CATEGORY)

            logger.info("Resolving & provisioning Discord guild roles...")
            role_mapping = await resolve_and_provision_guild_roles(discord_guild, manifest.squads)
        except Exception as e:
            logger.error("Discord initialization failed: %s", e)
            await discord_client.close()
            raise

    # 3. Seed Database
    logger.info("Seeding PostgreSQL database tables...")
    if session_factory is None:
        await init_db()
    db_result = await seed_database(
        manifest=manifest,
        guild_id=target_guild_id,
        session_factory=sess_factory,
        role_mapping=role_mapping,
        actor_discord_id=int(settings.DISCORD_CLIENT_ID or 10001),
        reset=reset,
    )
    logger.info(
        "Database seeded: %d projects, %d squads, %d tasks created",
        len(db_result.projects_by_prefix),
        len(db_result.squads_by_name),
        db_result.created_tasks_count,
    )

    # 4. Provision Discord Workspaces (channels, tags, pinned hubs, action cards)
    if use_discord and discord_guild and discord_client:
        logger.info("Provisioning Discord forum channels, pinned hubs, and task thread cards...")
        task_repo = PostgresTaskRepo(sess_factory)
        project_repo = PostgresProjectRepo(sess_factory)
        outbox_repo = PostgresOutboxRepo(sess_factory)
        squad_repo = PostgresSquadRepo(sess_factory)
        user_repo = PostgresUserPreferenceRepo(sess_factory)
        uow = SqlAlchemyUnitOfWork(sess_factory)

        project_service = ProjectService(project_repo)
        squad_service = SquadService(squad_repo)
        user_service = UserService(user_repo)
        outbox_service = OutboxService(outbox_repo)
        task_service = TaskService(task_repo, project_service, outbox_service, uow=uow)

        try:
            await provision_discord_workspaces(
                guild=discord_guild,
                manifest=manifest,
                db_result=db_result,
                task_service=task_service,
                squad_service=squad_service,
                user_service=user_service,
                category_name=DEFAULT_MANAGED_CATEGORY,
            )
            logger.info("Discord workspaces provisioned successfully.")
        finally:
            await discord_client.close()

    return db_result


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed declarative development state for dgg-pm.")
    default_guild = settings.DISCORD_GUILD_ID or 1543430283250901023
    parser.add_argument(
        "--guild-id",
        type=int,
        default=default_guild,
        help=f"Target Discord Guild ID (default: {default_guild})",
    )
    parser.add_argument(
        "--profile",
        type=str,
        default=None,
        help="Optional seed profile overlay name from seeds/profiles/ (e.g., 'tech-tree', 'scale-test', 'all')",
    )
    parser.add_argument(
        "--seeds-dir",
        type=Path,
        default=DEFAULT_SEEDS_DIR,
        help=f"Path to seeds directory (default: {DEFAULT_SEEDS_DIR})",
    )
    parser.add_argument(
        "--no-discord",
        action="store_true",
        help="Run database seeding only without calling Discord APIs",
    )
    parser.add_argument(
        "--no-reset",
        action="store_true",
        help="Do not purge existing database tables and Discord category before seeding",
    )

    args = parser.parse_args()

    async def _async_main():
        try:
            await run_seed(
                seeds_dir=args.seeds_dir,
                guild_id=args.guild_id,
                profile_name=args.profile,
                no_discord=args.no_discord,
                reset=not args.no_reset,
            )
        finally:
            await close_db()

    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
