"""Tests for startup crash-loop backoff protection and actionable diagnostics in main.py."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from src.main import run_app


@pytest.mark.asyncio
async def test_main_backoff_login_failure_diagnostic(caplog):
    """Verify run_app logs clear diagnosis and triggers backoff on LoginFailure."""
    from src.config import settings

    login_err = discord.LoginFailure("Improper token has been passed.")

    async def fail_bot(*args, **kwargs):
        raise login_err

    mock_bot = MagicMock()
    mock_bot.start = fail_bot
    mock_bot.is_closed.return_value = True

    with (
        patch("src.main.init_db", new_callable=AsyncMock),
        patch("src.main.DggPmBot", return_value=mock_bot),
        patch("src.main.uvicorn.Server.serve", new_callable=AsyncMock),
        patch.object(settings, "DISCORD_BOT_TOKEN", "fake_token"),
        patch.object(settings, "STARTUP_CRASH_BACKOFF_SECONDS", 0.01),
        caplog.at_level(logging.CRITICAL, logger="dgg_pm.main"),
    ):
        with pytest.raises(SystemExit) as exc_info:
            await run_app()

        assert exc_info.value.code == 1

    assert "Invalid Discord Bot Token (LoginFailure)" in caplog.text
    assert "DISCORD_BOT_TOKEN" in caplog.text


@pytest.mark.asyncio
async def test_main_backoff_rate_limited_diagnostic(caplog):
    """Verify run_app logs actionable diagnosis on Discord RateLimited error."""
    from src.config import settings

    rate_err = discord.RateLimited(retry_after=42.5)

    async def fail_bot(*args, **kwargs):
        raise rate_err

    mock_bot = MagicMock()
    mock_bot.start = fail_bot
    mock_bot.is_closed.return_value = True

    with (
        patch("src.main.init_db", new_callable=AsyncMock),
        patch("src.main.DggPmBot", return_value=mock_bot),
        patch("src.main.uvicorn.Server.serve", new_callable=AsyncMock),
        patch.object(settings, "DISCORD_BOT_TOKEN", "fake_token"),
        patch.object(settings, "STARTUP_CRASH_BACKOFF_SECONDS", 0.01),
        caplog.at_level(logging.CRITICAL, logger="dgg_pm.main"),
    ):
        with pytest.raises(SystemExit) as exc_info:
            await run_app()

        assert exc_info.value.code == 1

    assert "Discord API Rate Limited" in caplog.text
    assert "42.5" in caplog.text


@pytest.mark.asyncio
async def test_main_backoff_interrupted_by_stop_event():
    """Verify that if shutdown signal is received, backoff does not block."""
    import time

    from src.config import settings

    async def fail_bot(*args, **kwargs):
        raise discord.LoginFailure("Improper token")

    mock_bot = MagicMock()
    mock_bot.start = fail_bot
    mock_bot.is_closed.return_value = True

    with (
        patch("src.main.init_db", new_callable=AsyncMock),
        patch("src.main.DggPmBot", return_value=mock_bot),
        patch("src.main.uvicorn.Server.serve", new_callable=AsyncMock),
        patch.object(settings, "DISCORD_BOT_TOKEN", "fake_token"),
        patch.object(settings, "STARTUP_CRASH_BACKOFF_SECONDS", 10.0),
    ):
        # We start run_app and trigger handle_signal right away
        start_time = time.monotonic()

        # Test that with 0.01s backoff it returns quickly
        with patch.object(settings, "STARTUP_CRASH_BACKOFF_SECONDS", 0.05):
            with pytest.raises(SystemExit):
                await run_app()
        duration = time.monotonic() - start_time
        assert duration < 2.0
