import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.adapters.discord_bot.menu_manager import MenuSessionManager


@pytest.mark.asyncio
async def test_register_menu_supersedes_previous_menu():
    manager = MenuSessionManager()

    inter1 = MagicMock()
    inter1.guild_id = 111
    inter1.user.id = 999
    inter1.delete_original_response = AsyncMock()

    inter2 = MagicMock()
    inter2.guild_id = 111
    inter2.user.id = 999
    inter2.delete_original_response = AsyncMock()

    await manager.register_menu(inter1)
    inter1.delete_original_response.assert_not_awaited()

    # User opens second menu -> first menu is dismissed
    await manager.register_menu(inter2)
    inter1.delete_original_response.assert_awaited_once()
    inter2.delete_original_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_register_menu_independent_users():
    manager = MenuSessionManager()

    user1_inter = MagicMock()
    user1_inter.guild_id = 111
    user1_inter.user.id = 100
    user1_inter.delete_original_response = AsyncMock()

    user2_inter = MagicMock()
    user2_inter.guild_id = 111
    user2_inter.user.id = 200
    user2_inter.delete_original_response = AsyncMock()

    await manager.register_menu(user1_inter)
    await manager.register_menu(user2_inter)

    user1_inter.delete_original_response.assert_not_awaited()
    user2_inter.delete_original_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_unregister_menu():
    manager = MenuSessionManager()

    inter = MagicMock()
    inter.guild_id = 111
    inter.user.id = 999
    inter.delete_original_response = AsyncMock()

    await manager.register_menu(inter)
    manager.unregister_menu(inter)

    inter_next = MagicMock()
    inter_next.guild_id = 111
    inter_next.user.id = 999
    inter_next.delete_original_response = AsyncMock()

    # Opening another menu after unregistering does not call delete on the old one
    await manager.register_menu(inter_next)
    inter.delete_original_response.assert_not_awaited()


@pytest.mark.asyncio
async def test_schedule_toast_dismissal():
    manager = MenuSessionManager()
    inter = MagicMock()
    inter.delete_original_response = AsyncMock()

    # Schedule with tiny delay for fast test
    manager.schedule_toast_dismissal(inter, delay=0.01)
    inter.delete_original_response.assert_not_awaited()

    await asyncio.sleep(0.02)
    inter.delete_original_response.assert_awaited_once()
