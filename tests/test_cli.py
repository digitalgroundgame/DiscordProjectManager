"""Tests for standalone Admin CLI commands."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.cli import run_cli


@pytest.mark.asyncio
async def test_cli_sync_commands_requires_token(capsys):
    """Verify sync-commands exits with error code 1 when DISCORD_BOT_TOKEN is missing."""
    with patch("src.config.settings.DISCORD_BOT_TOKEN", ""):
        code = await run_cli(["sync-commands"])
        assert code == 1
        captured = capsys.readouterr()
        assert "DISCORD_BOT_TOKEN" in captured.err or "DISCORD_BOT_TOKEN" in captured.out


@pytest.mark.asyncio
async def test_cli_sync_commands_guild_override():
    """Verify sync-commands syncs to specified guild when --guild-id is passed."""
    mock_bot = MagicMock()
    mock_bot.add_cog = AsyncMock()
    mock_bot.login = AsyncMock()
    mock_bot.close = AsyncMock()
    mock_bot.sync_slash_commands = AsyncMock(return_value=[MagicMock(), MagicMock()])

    with (
        patch("src.config.settings.DISCORD_BOT_TOKEN", "mock_token"),
        patch("src.cli.create_cli_bot", return_value=mock_bot),
    ):
        code = await run_cli(["sync-commands", "--guild-id", "123456789"])
        assert code == 0
        mock_bot.add_cog.assert_awaited_once()
        mock_bot.login.assert_awaited_once_with("mock_token")
        mock_bot.sync_slash_commands.assert_awaited_once_with(guild_id=123456789)
        mock_bot.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_cli_sync_commands_global_flag():
    """Verify sync-commands syncs globally when --global is passed."""
    mock_bot = MagicMock()
    mock_bot.add_cog = AsyncMock()
    mock_bot.login = AsyncMock()
    mock_bot.close = AsyncMock()
    mock_bot.sync_slash_commands = AsyncMock(return_value=[MagicMock()])

    with (
        patch("src.config.settings.DISCORD_BOT_TOKEN", "mock_token"),
        patch("src.config.settings.DISCORD_GUILD_ID", 111222333),
        patch("src.cli.create_cli_bot", return_value=mock_bot),
    ):
        code = await run_cli(["sync-commands", "--global"])
        assert code == 0
        mock_bot.sync_slash_commands.assert_awaited_once_with(guild_id=None)


@pytest.mark.asyncio
async def test_cli_sync_commands_preserves_gateway_sessions():
    """Verify sync-commands uses HTTP login only and does not invoke Gateway connect or start."""
    mock_bot = MagicMock()
    mock_bot.add_cog = AsyncMock()
    mock_bot.login = AsyncMock()
    mock_bot.connect = AsyncMock()
    mock_bot.start = AsyncMock()
    mock_bot.close = AsyncMock()
    mock_bot.sync_slash_commands = AsyncMock(return_value=[])

    with (
        patch("src.config.settings.DISCORD_BOT_TOKEN", "mock_token"),
        patch("src.cli.create_cli_bot", return_value=mock_bot),
    ):
        code = await run_cli(["sync-commands", "--global"])
        assert code == 0
        mock_bot.login.assert_awaited_once()
        mock_bot.connect.assert_not_called()
        mock_bot.start.assert_not_called()
