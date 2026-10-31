import asyncio
from datetime import UTC
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


@pytest.mark.asyncio
async def test_schedule_toast_dismissal_webhook_message():
    manager = MenuSessionManager()
    toast = MagicMock()
    toast.delete = AsyncMock()

    manager.schedule_toast_dismissal(toast, delay=0.01)
    toast.delete.assert_not_awaited()

    await asyncio.sleep(0.02)
    toast.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_schedule_toast_dismissal_none():
    manager = MenuSessionManager()
    manager.schedule_toast_dismissal(None, delay=0.01)
    assert len(manager._background_tasks) == 0


def test_attach_dismissal_notice_no_existing_description():
    from datetime import datetime

    import discord

    from src.adapters.discord_bot.menu_manager import attach_dismissal_notice

    base_time = datetime(2026, 9, 12, 10, 0, 0, tzinfo=UTC)
    target_ts = int(base_time.timestamp()) + 8

    embed = discord.Embed(title="Toast Title")
    result = attach_dismissal_notice(embed, delay=8.0, base_time=base_time)

    assert result is embed
    assert result.description == f"*Auto-dismisses <t:{target_ts}:R>*"


def test_attach_dismissal_notice_with_existing_description():
    from datetime import datetime

    import discord

    from src.adapters.discord_bot.menu_manager import attach_dismissal_footer

    base_time = datetime(2026, 9, 12, 10, 0, 0, tzinfo=UTC)
    target_ts = int(base_time.timestamp()) + 10

    embed = discord.Embed(title="Task Created", description="Details")
    embed.set_footer(text="Task UUID: 12345", icon_url="https://example.com/icon.png")
    result = attach_dismissal_footer(embed, delay=10.0, base_time=base_time)

    assert result.description == f"Details\n\n*Auto-dismisses <t:{target_ts}:R>*"
    assert result.footer.text == "Task UUID: 12345"
    assert result.footer.icon_url == "https://example.com/icon.png"


def test_format_toast_message():
    from datetime import datetime

    from src.adapters.discord_bot.menu_manager import format_toast_message

    base_time = datetime(2026, 9, 12, 10, 0, 0, tzinfo=UTC)
    target_ts = int(base_time.timestamp()) + 10

    msg = "❌ Invalid date expression: `xyz`"
    formatted = format_toast_message(msg, delay=10.0, base_time=base_time)

    assert formatted == f"❌ Invalid date expression: `xyz`\n\n*Auto-dismisses <t:{target_ts}:R>*"


def test_strip_status_emojis():
    from src.adapters.discord_bot.menu_manager import strip_status_emojis

    assert strip_status_emojis("✅ Note added to **API-1**.") == "Note added to **API-1**."
    assert strip_status_emojis("❌ Failed to update status: Error") == "Failed to update status: Error"
    assert strip_status_emojis("⚠️ Task was modified") == "Task was modified"
    assert strip_status_emojis("🗑️ Task permanently deleted") == "Task permanently deleted"
    assert strip_status_emojis("Plain text without emoji") == "Plain text without emoji"


def test_build_toast_embed_success_and_error():
    from datetime import datetime

    import discord

    from src.adapters.discord_bot.menu_manager import ToastType, build_toast_embed

    base_time = datetime(2026, 9, 12, 10, 0, 0, tzinfo=UTC)
    target_ts = int(base_time.timestamp()) + 6

    # Success: Green embed, stripped leading emoji, countdown attached
    embed = build_toast_embed(
        "✅ Note added to **API-1**.", toast_type=ToastType.SUCCESS, delay=6.0, base_time=base_time
    )
    assert embed.color == discord.Color.green()
    assert embed.description == f"Note added to **API-1**.\n\n*Auto-dismisses <t:{target_ts}:R>*"

    # Error: Red embed
    err_embed = build_toast_embed(
        "❌ Note cannot be empty.", toast_type=ToastType.ERROR, delay=10.0, base_time=base_time
    )
    err_ts = int(base_time.timestamp()) + 10
    assert err_embed.color == discord.Color.red()
    assert err_embed.description == f"Note cannot be empty.\n\n*Auto-dismisses <t:{err_ts}:R>*"

    # Warning: Gold embed
    warn_embed = build_toast_embed(
        "⚠️ Dependency bypassed", toast_type=ToastType.WARNING, delay=6.0, base_time=base_time
    )
    assert warn_embed.color == discord.Color.gold()
    assert warn_embed.description == f"Dependency bypassed\n\n*Auto-dismisses <t:{target_ts}:R>*"

    # Info: Blurple embed
    info_embed = build_toast_embed("ℹ️ Action cancelled", toast_type=ToastType.INFO, delay=6.0, base_time=base_time)
    assert info_embed.color == discord.Color.blurple()
    assert info_embed.description == f"Action cancelled\n\n*Auto-dismisses <t:{target_ts}:R>*"


@pytest.mark.asyncio
async def test_send_toast_unresponded_and_deferred(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    import discord

    from src.adapters.discord_bot.menu_manager import ToastType, get_menu_manager, send_toast

    scheduled = []
    monkeypatch.setattr(
        get_menu_manager(),
        "schedule_toast_dismissal",
        lambda target, delay=6.0: scheduled.append(target),
    )

    # 1. Unresponded interaction uses response.send_message
    interaction1 = MagicMock(spec=discord.Interaction)
    interaction1.response = MagicMock()
    interaction1.response.is_done.return_value = False
    interaction1.response.send_message = AsyncMock()

    await send_toast(interaction1, "Task created successfully", toast_type=ToastType.SUCCESS)
    interaction1.response.send_message.assert_awaited_once()
    assert interaction1.response.send_message.call_args.kwargs.get("ephemeral") is True
    embed = interaction1.response.send_message.call_args.kwargs.get("embed")
    assert embed is not None
    assert embed.color == discord.Color.green()
    assert "Task created successfully" in embed.description
    assert len(scheduled) == 1
    assert scheduled[0] is interaction1

    # 2. Deferred interaction uses followup.send
    scheduled.clear()
    interaction2 = MagicMock(spec=discord.Interaction)
    interaction2.response = MagicMock()
    interaction2.response.is_done.return_value = True
    followup_msg = MagicMock(spec=discord.WebhookMessage)
    interaction2.followup = MagicMock()
    interaction2.followup.send = AsyncMock(return_value=followup_msg)

    await send_toast(interaction2, "Something went wrong", toast_type=ToastType.ERROR, delay=10.0)
    interaction2.followup.send.assert_awaited_once()
    assert interaction2.followup.send.call_args.kwargs.get("ephemeral") is True
    assert interaction2.followup.send.call_args.kwargs.get("wait") is True
    embed2 = interaction2.followup.send.call_args.kwargs.get("embed")
    assert embed2 is not None
    assert embed2.color == discord.Color.red()
    assert "Something went wrong" in embed2.description
    assert len(scheduled) == 1
    assert scheduled[0] is followup_msg


@pytest.mark.asyncio
async def test_edit_toast_unresponded_and_responded(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    import discord

    from src.adapters.discord_bot.menu_manager import ToastType, edit_toast, get_menu_manager

    scheduled = []
    monkeypatch.setattr(
        get_menu_manager(),
        "schedule_toast_dismissal",
        lambda target, delay=6.0: scheduled.append(target),
    )

    # 1. Unresponded interaction edits via response.edit_message
    interaction1 = MagicMock(spec=discord.Interaction)
    interaction1.response = MagicMock()
    interaction1.response.is_done.return_value = False
    interaction1.response.edit_message = AsyncMock()

    await edit_toast(interaction1, "Cancelled action", toast_type=ToastType.INFO)
    interaction1.response.edit_message.assert_awaited_once()
    call_kwargs = interaction1.response.edit_message.call_args.kwargs
    assert call_kwargs.get("view") is None
    assert call_kwargs.get("content") is None
    embed = call_kwargs.get("embed")
    assert embed is not None
    assert embed.color == discord.Color.blurple()
    assert "Cancelled action" in embed.description
    assert len(scheduled) == 1

    # 2. Responded interaction edits via edit_original_response
    interaction2 = MagicMock(spec=discord.Interaction)
    interaction2.response = MagicMock()
    interaction2.response.is_done.return_value = True
    interaction2.edit_original_response = AsyncMock()

    await edit_toast(interaction2, "Updated successfully", toast_type=ToastType.SUCCESS)
    interaction2.edit_original_response.assert_awaited_once()
    call_kwargs2 = interaction2.edit_original_response.call_args.kwargs
    assert call_kwargs2.get("view") is None
    assert call_kwargs2.get("content") is None
    embed2 = call_kwargs2.get("embed")
    assert embed2 is not None
    assert embed2.color == discord.Color.green()
