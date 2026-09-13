from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import discord
import pytest

from src.adapters.discord_bot.cogs.pm_cog import PmCog
from src.adapters.discord_bot.workspace_protocol import SyncWorkspaceResult
from src.domain.enums import PriorityLevel, TaskStatus
from src.domain.models import Task


@pytest.mark.asyncio
async def test_pm_task_update_command_immediate_rename():
    bot = MagicMock(spec=discord.Client)
    task_service = MagicMock()
    project_service = MagicMock()
    auth_service = MagicMock()
    auth_service.require_task_mutation = AsyncMock()
    workspace = MagicMock()
    workspace.sync_workspace = AsyncMock(
        return_value=SyncWorkspaceResult(
            success=True,
            title_renamed=True,
            title_deferred=False,
            cooldown_remaining_seconds=0.0,
        )
    )

    cog = PmCog(
        bot=bot,
        task_service=task_service,
        project_service=project_service,
        auth_service=auth_service,
        workspace=workspace,
    )

    task = Task(
        id=uuid4(),
        guild_id=12345,
        short_id="PRJ-1",
        title="Old Title",
        status=TaskStatus.NOT_STARTED,
        priority=PriorityLevel.NORMAL,
        creator_discord_id=999,
        discord_thread_id=555,
    )
    updated_task = Task(
        id=task.id,
        guild_id=12345,
        short_id="PRJ-1",
        title="New Title",
        status=TaskStatus.NOT_STARTED,
        priority=PriorityLevel.NORMAL,
        creator_discord_id=999,
        discord_thread_id=555,
    )
    task_service.get_by_short_id = AsyncMock(return_value=task)
    task_service.update_details = AsyncMock(return_value=updated_task)

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock(spec=discord.Guild, id=12345)
    interaction.user = MagicMock(spec=discord.Member, id=999)
    interaction.channel = MagicMock(spec=discord.Thread, id=555)
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()

    await cog.task_update.callback(cog, interaction, task="PRJ-1", title="New Title")

    task_service.update_details.assert_awaited_once()
    workspace.sync_workspace.assert_awaited_once_with(updated_task, sync_title=True, sync_starter_card=True)
    interaction.followup.send.assert_awaited_once()
    call_args = interaction.followup.send.call_args[0]
    msg = call_args[0]
    assert "Updated details for **[PRJ-1]**" in msg
    assert "Discord limits thread renames" not in msg


@pytest.mark.asyncio
async def test_pm_task_update_command_deferred_rename_notice():
    bot = MagicMock(spec=discord.Client)
    task_service = MagicMock()
    project_service = MagicMock()
    auth_service = MagicMock()
    auth_service.require_task_mutation = AsyncMock()
    workspace = MagicMock()
    # Simulate deferred rename due to Discord cooldown
    workspace.sync_workspace = AsyncMock(
        return_value=SyncWorkspaceResult(
            success=True,
            title_renamed=False,
            title_deferred=True,
            cooldown_remaining_seconds=480.0,
        )
    )

    cog = PmCog(
        bot=bot,
        task_service=task_service,
        project_service=project_service,
        auth_service=auth_service,
        workspace=workspace,
    )

    task = Task(
        id=uuid4(),
        guild_id=12345,
        short_id="PRJ-2",
        title="Original Title",
        status=TaskStatus.NOT_STARTED,
        priority=PriorityLevel.NORMAL,
        creator_discord_id=999,
        discord_thread_id=556,
    )
    updated_task = Task(
        id=task.id,
        guild_id=12345,
        short_id="PRJ-2",
        title="Rapid Title Update",
        status=TaskStatus.NOT_STARTED,
        priority=PriorityLevel.NORMAL,
        creator_discord_id=999,
        discord_thread_id=556,
    )
    task_service.get_by_short_id = AsyncMock(return_value=task)
    task_service.update_details = AsyncMock(return_value=updated_task)

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock(spec=discord.Guild, id=12345)
    interaction.user = MagicMock(spec=discord.Member, id=999)
    interaction.channel = MagicMock(spec=discord.Thread, id=556)
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()

    await cog.task_update.callback(cog, interaction, task="PRJ-2", title="Rapid Title Update")

    interaction.followup.send.assert_awaited_once()
    call_args = interaction.followup.send.call_args[0]
    msg = call_args[0]
    assert "Updated details for **[PRJ-2]**" in msg
    assert "Discord limits thread renames to 2 per 10 minutes" in msg
