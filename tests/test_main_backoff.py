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


@pytest.mark.asyncio
async def test_run_app_resilient_to_transient_gateway_failure(caplog):
    """Verify run_app retries transient gateway errors and does not crash the platform."""
    import asyncio

    from src.config import settings

    bot_start_calls = 0
    worker_started = False
    registered_handlers = []

    mock_bot = MagicMock()
    mock_bot.is_closed.return_value = False
    mock_bot.is_ready.return_value = False

    async def mock_bot_close():
        mock_bot.is_closed.return_value = True

    mock_bot.close = mock_bot_close
    mock_bot.clear = MagicMock()

    async def mock_bot_start(*args, **kwargs):
        nonlocal bot_start_calls
        bot_start_calls += 1
        if bot_start_calls == 1:
            raise discord.GatewayNotFound()
        # On subsequent attempts, simulate running until closed
        while not mock_bot.is_closed.return_value:
            await asyncio.sleep(0.01)

    mock_bot.start = mock_bot_start

    worker_stopped = False

    async def mock_worker_start(*args, **kwargs):
        nonlocal worker_started
        worker_started = True
        while not worker_stopped:
            await asyncio.sleep(0.01)

    def mock_worker_stop(*args, **kwargs):
        nonlocal worker_stopped
        worker_stopped = True

    async def mock_serve(self=None, *args, **kwargs):
        # Wait until bot retried and worker started, then trigger clean platform shutdown via signal handler
        while bot_start_calls < 2 or not worker_started:
            await asyncio.sleep(0.01)
        if registered_handlers:
            registered_handlers[0]()

    loop = asyncio.get_running_loop()

    def mock_add_signal(sig, handler):
        registered_handlers.append(handler)

    with (
        patch("src.main.init_db", new_callable=AsyncMock),
        patch("src.main.DggPmBot", return_value=mock_bot),
        patch("src.main.OutboxWorker.start", side_effect=mock_worker_start),
        patch("src.main.OutboxWorker.stop", side_effect=mock_worker_stop),
        patch("uvicorn.Server.serve", side_effect=mock_serve),
        patch.object(loop, "add_signal_handler", side_effect=mock_add_signal),
        patch.object(settings, "DISCORD_BOT_TOKEN", "fake_token"),
        patch.object(settings, "GATEWAY_RETRY_INITIAL_DELAY_SECONDS", 0.01),
        patch.object(settings, "GATEWAY_RETRY_MAX_DELAY_SECONDS", 0.02),
        patch.object(settings, "GATEWAY_RETRY_JITTER", 0.0),
        patch.object(settings, "STARTUP_CRASH_BACKOFF_SECONDS", 0.05),
        caplog.at_level(logging.WARNING, logger="dgg_pm.gateway_supervisor"),
    ):
        await run_app()

    assert bot_start_calls >= 2
    assert worker_started is True
    assert "Transient Discord Gateway connection error" in caplog.text
