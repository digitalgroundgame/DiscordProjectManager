"""Resilient Discord Gateway connection supervisor with exponential backoff and jitter."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import aiohttp
import discord

if TYPE_CHECKING:
    from discord.ext import commands

logger = logging.getLogger("dgg_pm.gateway_supervisor")


def is_transient_gateway_error(exc: Exception) -> bool:
    """Determine whether an exception encountered during bot connection is transient or fatal.

    Transient errors (network connection drops, timeouts, Discord 5xx outages, GatewayNotFound)
    can be safely retried. Fatal configuration/auth errors (LoginFailure, Missing Access,
    RateLimited, PrivilegedIntentsRequired) should fail fast.
    """
    if isinstance(exc, (discord.errors.LoginFailure, discord.errors.RateLimited)):
        return False

    if isinstance(exc, discord.errors.Forbidden):
        # 50001 = Missing Access (bot not added to guild or missing scopes)
        return False

    if isinstance(exc, discord.errors.PrivilegedIntentsRequired):
        return False

    if isinstance(
        exc,
        (
            discord.errors.GatewayNotFound,
            discord.errors.DiscordServerError,
            discord.errors.ConnectionClosed,
            aiohttp.ClientError,
            ConnectionError,
            OSError,
            TimeoutError,
            asyncio.TimeoutError,
        ),
    ):
        return True

    if isinstance(exc, discord.errors.HTTPException):
        if exc.status is not None and exc.status >= 500:
            return True
        return False

    return False


def calculate_backoff_delay(
    attempt: int,
    initial_delay: float = 2.0,
    max_delay: float = 60.0,
    backoff_factor: float = 2.0,
    jitter: float = 0.2,
) -> float:
    """Calculate exponential backoff delay with bounded jitter.

    Parameters:
        attempt: 1-indexed retry attempt number.
        initial_delay: Starting delay in seconds for attempt 1.
        max_delay: Ceiling backoff delay in seconds.
        backoff_factor: Exponential multiplier per attempt.
        jitter: Proportional randomized jitter factor (e.g. 0.2 for +/- 20%).
    """
    raw_delay = initial_delay * (backoff_factor ** max(0, attempt - 1))
    capped_delay = min(max_delay, raw_delay)
    if jitter > 0:
        import random

        delta = random.uniform(-jitter * capped_delay, jitter * capped_delay)
        return max(0.0, capped_delay + delta)
    return capped_delay


async def start_bot_with_retry(
    bot: commands.Bot,
    token: str,
    stop_event: asyncio.Event | None = None,
    initial_delay: float = 2.0,
    max_delay: float = 60.0,
    backoff_factor: float = 2.0,
    jitter: float = 0.2,
    max_retries: int | None = None,
) -> None:
    """Supervise Discord bot connection with exponential backoff and jitter on transient failures.

    Parameters:
        bot: Discord bot instance to start.
        token: Discord bot authentication token.
        stop_event: Platform lifecycle event triggering graceful termination.
        initial_delay: Starting retry backoff in seconds.
        max_delay: Maximum retry backoff ceiling in seconds.
        backoff_factor: Multiplier for exponential backoff per attempt.
        jitter: Randomized jitter factor.
        max_retries: Optional maximum attempts before re-raising the transient error (None = indefinite).
    """
    if stop_event is None:
        stop_event = asyncio.Event()

    attempt = 0

    while not stop_event.is_set():
        attempt += 1
        logger.info("Attempting Discord Gateway connection (attempt %d)...", attempt)
        try:
            await bot.start(token)
            # When bot.start() completes cleanly (e.g. via bot.close() on shutdown), exit loop
            break
        except asyncio.CancelledError:
            logger.info("Discord Gateway supervisor task cancelled.")
            raise
        except Exception as exc:
            if not is_transient_gateway_error(exc):
                logger.error("Fatal Discord Gateway error encountered: %s (%s)", type(exc).__name__, exc)
                raise

            was_ready = False
            try:
                if hasattr(bot, "is_ready") and bot.is_ready():
                    was_ready = True
            except Exception:
                pass

            if was_ready:
                attempt = 1

            if max_retries is not None and attempt >= max_retries:
                logger.critical(
                    "Exhausted maximum Discord Gateway connection attempts (%d). Re-raising %s.",
                    max_retries,
                    type(exc).__name__,
                )
                raise

            # Clean up connection resources before next attempt
            try:
                if hasattr(bot, "is_closed") and not bot.is_closed():
                    await bot.close()
            except Exception as close_exc:
                logger.debug("Error while closing bot session during retry cleanup: %s", close_exc)

            if hasattr(bot, "clear"):
                bot.clear()

            delay = calculate_backoff_delay(
                attempt=attempt,
                initial_delay=initial_delay,
                max_delay=max_delay,
                backoff_factor=backoff_factor,
                jitter=jitter,
            )

            logger.warning(
                "Transient Discord Gateway connection error on attempt %d (%s: %s). Retrying in %.2fs...",
                attempt,
                type(exc).__name__,
                exc,
                delay,
            )

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
                logger.info("Stop event received during gateway retry backoff. Aborting reconnect loop.")
                break
            except TimeoutError:
                pass
