"""Tests for Discord Gateway supervisor and startup connection retry loop."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import discord
import pytest

from src.adapters.discord_bot.gateway_supervisor import (
    calculate_backoff_delay,
    is_transient_gateway_error,
    start_bot_with_retry,
)


def test_is_transient_gateway_error_classification():
    """Verify that is_transient_gateway_error correctly separates transient network/5xx from fatal auth errors."""
    # 1. Transient errors -> True
    mock_resp_503 = MagicMock()
    mock_resp_503.status = 503
    mock_resp_503.reason = "Service Unavailable"
    http_503_err = discord.HTTPException(mock_resp_503, "Service Unavailable")

    mock_resp_500 = MagicMock()
    mock_resp_500.status = 500
    mock_resp_500.reason = "Internal Server Error"
    server_err = discord.DiscordServerError(mock_resp_500, "Internal Server Error")

    gateway_not_found = discord.GatewayNotFound()
    conn_closed = discord.ConnectionClosed(MagicMock(code=1006, reason="abnormal closure"), shard_id=0)
    aiohttp_err = aiohttp.ClientConnectorError(
        connection_key=MagicMock(),
        os_error=OSError("Cannot connect to host"),
    )
    timeout_err = TimeoutError("Connection timed out")
    conn_reset = ConnectionResetError("Connection reset by peer")

    assert is_transient_gateway_error(http_503_err) is True
    assert is_transient_gateway_error(server_err) is True
    assert is_transient_gateway_error(gateway_not_found) is True
    assert is_transient_gateway_error(conn_closed) is True
    assert is_transient_gateway_error(aiohttp_err) is True
    assert is_transient_gateway_error(timeout_err) is True
    assert is_transient_gateway_error(conn_reset) is True

    # 2. Fatal errors -> False
    login_err = discord.LoginFailure("Improper token has been passed.")
    rate_err = discord.RateLimited(retry_after=10.0)

    mock_resp_403 = MagicMock()
    mock_resp_403.status = 403
    mock_resp_403.reason = "Forbidden"
    forbidden_err = discord.Forbidden(mock_resp_403, "Missing Access")
    forbidden_err.code = 50001

    intents_err = discord.PrivilegedIntentsRequired(discord.Intents.all())
    value_err = ValueError("Invalid parameter")

    assert is_transient_gateway_error(login_err) is False
    assert is_transient_gateway_error(rate_err) is False
    assert is_transient_gateway_error(forbidden_err) is False
    assert is_transient_gateway_error(intents_err) is False
    assert is_transient_gateway_error(value_err) is False


def test_calculate_backoff_delay():
    """Verify exponential progression, capping at max_delay, and jitter bounds."""
    # Deterministic check with jitter=0.0
    assert calculate_backoff_delay(1, initial_delay=2.0, max_delay=60.0, backoff_factor=2.0, jitter=0.0) == 2.0
    assert calculate_backoff_delay(2, initial_delay=2.0, max_delay=60.0, backoff_factor=2.0, jitter=0.0) == 4.0
    assert calculate_backoff_delay(3, initial_delay=2.0, max_delay=60.0, backoff_factor=2.0, jitter=0.0) == 8.0
    assert calculate_backoff_delay(4, initial_delay=2.0, max_delay=60.0, backoff_factor=2.0, jitter=0.0) == 16.0
    assert calculate_backoff_delay(5, initial_delay=2.0, max_delay=60.0, backoff_factor=2.0, jitter=0.0) == 32.0
    assert calculate_backoff_delay(6, initial_delay=2.0, max_delay=60.0, backoff_factor=2.0, jitter=0.0) == 60.0
    assert calculate_backoff_delay(10, initial_delay=2.0, max_delay=60.0, backoff_factor=2.0, jitter=0.0) == 60.0

    # Jitter bounds check: with jitter=0.2, delay must stay within +/- 20%
    for _ in range(50):
        delay_1 = calculate_backoff_delay(1, initial_delay=2.0, max_delay=60.0, jitter=0.2)
        assert 1.6 <= delay_1 <= 2.4

        delay_cap = calculate_backoff_delay(10, initial_delay=2.0, max_delay=60.0, jitter=0.2)
        assert 48.0 <= delay_cap <= 72.0


@pytest.mark.asyncio
async def test_start_bot_with_retry_recovers_after_transient_error():
    """Verify start_bot_with_retry retries after a transient error and succeeds."""
    from unittest.mock import AsyncMock

    mock_bot = MagicMock()
    mock_bot.is_closed.return_value = False
    mock_bot.is_ready.return_value = False
    mock_bot.close = AsyncMock()
    mock_bot.clear = MagicMock()

    calls = 0

    async def mock_start(token: str):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise discord.GatewayNotFound()
        # Second call succeeds
        return

    mock_bot.start = mock_start
    stop_event = asyncio.Event()

    await start_bot_with_retry(
        bot=mock_bot,
        token="test_token",
        stop_event=stop_event,
        initial_delay=0.01,
        max_delay=0.05,
        jitter=0.0,
    )

    assert calls == 2
    mock_bot.close.assert_awaited_once()
    mock_bot.clear.assert_called_once()


@pytest.mark.asyncio
async def test_start_bot_with_retry_fails_fast_on_fatal_error():
    """Verify start_bot_with_retry immediately raises LoginFailure without retrying."""
    from unittest.mock import AsyncMock

    mock_bot = MagicMock()
    mock_bot.is_closed.return_value = True
    mock_bot.close = AsyncMock()
    mock_bot.clear = MagicMock()

    calls = 0

    async def mock_start(token: str):
        nonlocal calls
        calls += 1
        raise discord.LoginFailure("Improper token has been passed.")

    mock_bot.start = mock_start
    stop_event = asyncio.Event()

    with pytest.raises(discord.LoginFailure):
        await start_bot_with_retry(
            bot=mock_bot,
            token="test_token",
            stop_event=stop_event,
            initial_delay=0.01,
            max_delay=0.05,
        )

    assert calls == 1
    mock_bot.clear.assert_not_called()


@pytest.mark.asyncio
async def test_start_bot_with_retry_interrupted_by_stop_event():
    """Verify that setting stop_event immediately aborts the retry backoff delay."""
    from unittest.mock import AsyncMock

    mock_bot = MagicMock()
    mock_bot.is_closed.return_value = False
    mock_bot.is_ready.return_value = False
    mock_bot.close = AsyncMock()
    mock_bot.clear = MagicMock()

    async def mock_start(token: str):
        raise discord.GatewayNotFound()

    mock_bot.start = mock_start
    stop_event = asyncio.Event()

    # Schedule stop_event in background after 10ms
    asyncio.get_running_loop().call_later(0.01, stop_event.set)

    # initial_delay is long (10s), but stop_event should wake it up immediately
    await start_bot_with_retry(
        bot=mock_bot,
        token="test_token",
        stop_event=stop_event,
        initial_delay=10.0,
        max_delay=10.0,
    )

    assert stop_event.is_set()


@pytest.mark.asyncio
async def test_start_bot_with_retry_exhaustion_on_max_retries():
    """Verify that configuring max_retries raises the exception once attempts are exhausted."""
    mock_bot = MagicMock()
    mock_bot.is_closed.return_value = True
    mock_bot.is_ready.return_value = False
    mock_bot.close = MagicMock()
    mock_bot.clear = MagicMock()

    calls = 0

    async def mock_start(token: str):
        nonlocal calls
        calls += 1
        raise discord.GatewayNotFound()

    mock_bot.start = mock_start
    stop_event = asyncio.Event()

    with pytest.raises(discord.GatewayNotFound):
        await start_bot_with_retry(
            bot=mock_bot,
            token="test_token",
            stop_event=stop_event,
            initial_delay=0.01,
            max_delay=0.02,
            max_retries=3,
        )

    assert calls == 3


@pytest.mark.asyncio
async def test_start_bot_with_retry_resets_attempt_counter_after_ready():
    """Verify that if the bot was successfully connected (ready), subsequent disconnect resets backoff attempt."""
    mock_bot = MagicMock()
    mock_bot.is_closed.return_value = True
    mock_bot.close = AsyncMock()
    mock_bot.clear = MagicMock()

    calls = 0

    async def mock_start(token: str):
        nonlocal calls
        calls += 1
        if calls == 1:
            # 1st attempt fails before becoming ready
            mock_bot.is_ready.return_value = False
            raise discord.GatewayNotFound()
        elif calls == 2:
            # 2nd attempt connects, becomes ready, then disconnects
            mock_bot.is_ready.return_value = True
            raise discord.DiscordServerError(MagicMock(status=503), "Service Unavailable")
        # 3rd attempt succeeds
        mock_bot.is_ready.return_value = True
        return

    mock_bot.start = mock_start
    mock_bot.is_ready.return_value = False
    stop_event = asyncio.Event()

    with patch("src.adapters.discord_bot.gateway_supervisor.calculate_backoff_delay") as mock_calc:
        mock_calc.return_value = 0.01
        await start_bot_with_retry(
            bot=mock_bot,
            token="test_token",
            stop_event=stop_event,
            initial_delay=2.0,
        )
        assert mock_calc.call_count == 2
        # First retry was attempt 1
        assert mock_calc.call_args_list[0][1]["attempt"] == 1
        # Second retry was after being ready, so attempt reset back to 1 instead of 2
        assert mock_calc.call_args_list[1][1]["attempt"] == 1
