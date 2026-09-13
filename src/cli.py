"""Administrative and operational CLI commands for DGG-PM."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from src.adapters.discord_bot.bot import DggPmBot
from src.adapters.discord_bot.cogs.pm_cog import PmCog
from src.config import settings

logger = logging.getLogger("dgg_pm.cli")


def create_cli_bot() -> DggPmBot:
    """Instantiate a DggPmBot instance with registered cogs for CLI operations."""
    bot = DggPmBot()
    return bot


async def sync_commands(guild_id: int | None = None, is_global: bool = False) -> int:
    """Synchronize slash commands with Discord via HTTP without opening a Gateway connection."""
    token = settings.DISCORD_BOT_TOKEN
    if not token:
        logger.error("DISCORD_BOT_TOKEN is not configured. Cannot sync slash commands.")
        print("❌ Error: DISCORD_BOT_TOKEN is not set in environment or .env", file=sys.stderr)
        return 1

    target_guild_id = None if is_global else (guild_id or settings.DISCORD_GUILD_ID)

    bot = create_cli_bot()
    try:
        # Load cogs so application commands are populated in bot.tree
        await bot.add_cog(PmCog(bot=bot))

        # Perform HTTP login only (no Gateway WebSocket connection / no IDENTIFY count)
        await bot.login(token)

        scope_desc = f"guild {target_guild_id}" if target_guild_id else "globally across all servers"
        print(f"🔄 Synchronizing slash commands {scope_desc} via Discord HTTP API...")

        synced = await bot.sync_slash_commands(guild_id=target_guild_id)
        print(f"✅ Successfully synced {len(synced)} application slash commands {scope_desc}.")
        return 0
    except Exception as exc:
        logger.exception("Failed to sync slash commands via CLI: %s", exc)
        print(f"❌ Failed to sync slash commands: {exc}", file=sys.stderr)
        return 1
    finally:
        await bot.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.cli",
        description="Administrative CLI utilities for DGG-PM.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    sync_parser = subparsers.add_parser(
        "sync-commands",
        help="Synchronize slash commands with Discord without starting the full gateway bot.",
    )
    sync_parser.add_argument(
        "--guild-id",
        type=int,
        default=None,
        help="Specific Discord guild ID to sync commands to (defaults to DISCORD_GUILD_ID if not global).",
    )
    sync_parser.add_argument(
        "--global",
        dest="is_global",
        action="store_true",
        help="Sync slash commands globally across all Discord servers.",
    )

    return parser


async def run_cli(args: list[str] | None = None) -> int:
    parser = build_parser()
    parsed = parser.parse_args(args)

    if parsed.command == "sync-commands":
        return await sync_commands(guild_id=parsed.guild_id, is_global=parsed.is_global)

    return 0


def main():
    exit_code = asyncio.run(run_cli(sys.argv[1:]))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
