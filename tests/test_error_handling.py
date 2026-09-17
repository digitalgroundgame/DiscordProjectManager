import logging
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import discord
import pytest

from src.adapters.discord_bot.cogs.pm_cog import PmCog
from src.adapters.discord_bot.error_handler import send_interaction_error, translate_error
from src.domain.enums import PriorityLevel, TaskStatus
from src.domain.exceptions import (
    DggPmError,
    DomainError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    ProjectAlreadyExistsError,
    ProjectNotFoundError,
    SquadAlreadyExistsError,
    SquadNotFoundError,
    StaleVersionError,
    TaskNotFoundError,
    ValidationError,
)
from src.services.project_service import ProjectService
from src.services.squad_service import SquadService
from src.services.task_service import TaskService


def test_exception_hierarchy():
    """Verify inheritance relationships for typed domain exceptions."""
    assert issubclass(DomainError, DggPmError)
    assert issubclass(ValidationError, DomainError)
    assert issubclass(ValidationError, ValueError)

    assert issubclass(EntityNotFoundError, DomainError)
    assert issubclass(TaskNotFoundError, EntityNotFoundError)
    assert issubclass(ProjectNotFoundError, EntityNotFoundError)
    assert issubclass(SquadNotFoundError, EntityNotFoundError)

    assert issubclass(EntityAlreadyExistsError, DomainError)
    assert issubclass(ProjectAlreadyExistsError, EntityAlreadyExistsError)
    assert issubclass(ProjectAlreadyExistsError, ValueError)
    assert issubclass(SquadAlreadyExistsError, EntityAlreadyExistsError)
    assert issubclass(SquadAlreadyExistsError, ValueError)

    assert issubclass(StaleVersionError, DomainError)


def test_translate_error_known_exceptions():
    """Verify known business exceptions translate to friendly text without unexpected error flags."""
    # Stale version
    msg, unexpected = translate_error(StaleVersionError("Version conflict"), "updating task status")
    assert not unexpected
    assert "already modified by another user" in msg

    # Domain / Value errors
    msg, unexpected = translate_error(
        ProjectAlreadyExistsError("Project with name 'Core' already exists"), "creating project"
    )
    assert not unexpected
    assert msg == "❌ Project with name 'Core' already exists"

    msg, unexpected = translate_error(TaskNotFoundError("Task 'SEC-99' was not found."), "assigning task")
    assert not unexpected
    assert msg == "❌ Task 'SEC-99' was not found."

    # Discord forbidden (permissions)
    mock_resp = MagicMock()
    mock_resp.status = 403
    mock_resp.reason = "Forbidden"
    forbidden_err = discord.Forbidden(mock_resp, "Missing Permissions")
    msg, unexpected = translate_error(forbidden_err, "creating thread")
    assert not unexpected
    assert "lacks required Discord permissions" in msg


def test_translate_error_unexpected_sanitization():
    """Verify unknown/internal exceptions are sanitized to avoid leaking traces or SQL errors to UI."""
    internal_err = RuntimeError("SELECT * FROM tasks WHERE secret_token='xyz' - connection timed out")
    msg, unexpected = translate_error(internal_err, "creating task 'Deploy'")
    assert unexpected is True
    # Must NOT leak the SQL or secret or class name
    assert "secret_token" not in msg
    assert "RuntimeError" not in msg
    assert msg == "❌ An unexpected error occurred while creating task 'Deploy'. Please try again later."


@pytest.mark.asyncio
async def test_send_interaction_error_not_deferred(caplog):
    """Test send_interaction_error when interaction has not responded yet (uses response.send_message)."""
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.is_done.return_value = False
    interaction.response.send_message = AsyncMock()

    custom_logger = logging.getLogger("test_error_logger")

    try:
        raise KeyError("internal_state_missing")
    except Exception as raw_err:
        with caplog.at_level(logging.ERROR, logger="test_error_logger"):
            msg = await send_interaction_error(
                interaction, raw_err, "processing action", target_logger=custom_logger, ephemeral=True
            )

    interaction.response.send_message.assert_awaited_once_with(msg, ephemeral=True)
    assert "Unexpected error while processing action" in caplog.text
    assert "internal_state_missing" in caplog.text
    assert "KeyError" in caplog.text


@pytest.mark.asyncio
async def test_send_interaction_error_deferred():
    """Test send_interaction_error when interaction is already deferred/done (uses followup.send)."""
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.is_done.return_value = True
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()

    stale_err = StaleVersionError("Conflict")
    msg = await send_interaction_error(interaction, stale_err, "updating task", ephemeral=True)

    interaction.followup.send.assert_awaited_once_with(msg, ephemeral=True)
    assert "already modified" in msg


@pytest.mark.asyncio
async def test_service_raises_typed_exceptions(services):
    """Verify services raise strongly typed domain exceptions."""
    proj_srv: ProjectService = services["project"]
    task_srv: TaskService = services["task"]
    squad_srv: SquadService = services["squad"]
    guild_id = 8888888888

    # 1. Project Already Exists (name)
    await proj_srv.create_project(guild_id=guild_id, name="Security Alpha", prefix="SEC")
    with pytest.raises(ProjectAlreadyExistsError, match="name 'Security Alpha' already exists"):
        await proj_srv.create_project(guild_id=guild_id, name="Security Alpha", prefix="SEC2")

    # 2. Project Already Exists (prefix)
    with pytest.raises(ProjectAlreadyExistsError, match="is already in use"):
        await proj_srv.create_project(guild_id=guild_id, name="Security Beta", prefix="SEC")

    # 3. Squad Already Exists
    await squad_srv.create_squad(guild_id=guild_id, name="Red Squad", discord_role_id=1111)
    with pytest.raises(SquadAlreadyExistsError, match="name 'Red Squad' already exists"):
        await squad_srv.create_squad(guild_id=guild_id, name="Red Squad", discord_role_id=2222)

    # 4. Project Not Found on task creation
    fake_project_id = uuid4()
    with pytest.raises(ProjectNotFoundError, match="not found"):
        await task_srv.create_task(
            guild_id=guild_id,
            title="Invalid Task",
            project_id=fake_project_id,
            creator_discord_id=1001,
        )

    # 5. Task Not Found on updates
    fake_task_id = uuid4()
    with pytest.raises(TaskNotFoundError, match="does not exist"):
        await task_srv.update_status(fake_task_id, TaskStatus.COMPLETED, 1, 1001)

    with pytest.raises(TaskNotFoundError, match="does not exist"):
        await task_srv.update_priority(fake_task_id, PriorityLevel.HIGH, 1001)

    with pytest.raises(TaskNotFoundError, match="does not exist"):
        await task_srv.update_assignee(fake_task_id, 1002, 1001)

    with pytest.raises(TaskNotFoundError, match="does not exist"):
        await task_srv.update_details(fake_task_id, 1001, title="New Title")

    # 6. StaleVersionError on OCC conflict
    proj = await proj_srv.create_project(guild_id=guild_id, name="Ops", prefix="OPS")
    task = await task_srv.create_task(
        guild_id=guild_id,
        title="Setup Monitoring",
        project_id=proj.id,
        creator_discord_id=1001,
    )
    with pytest.raises(StaleVersionError, match="already modified"):
        await task_srv.update_status(
            task_id=task.id,
            new_status=TaskStatus.IN_PROGRESS,
            expected_version=999,  # Wrong version
            actor_discord_id=1001,
        )


@pytest.mark.asyncio
async def test_cogs_handle_service_and_unexpected_errors(services, caplog):
    """Test that cogs translate typed errors gracefully and safely sanitize unexpected exceptions."""
    bot = MagicMock(spec=discord.Client)
    pm_cog = PmCog(
        bot,
        project_service=services["project"],
        squad_service=services["squad"],
        task_service=services["task"],
    )

    guild_id = 9999999999
    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    interaction.user = MagicMock()
    interaction.user.id = 1001
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.response.is_done.return_value = True
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()

    # 1. Project Cog: Duplicate project name translates to clean error message
    await services["project"].create_project(guild_id=guild_id, name="Infra", prefix="INF")
    mock_channel = MagicMock(spec=discord.ForumChannel)
    mock_channel.id = 12345
    mock_channel.available_tags = []
    mock_role = MagicMock(spec=discord.Role)
    mock_role.id = 777111
    await pm_cog.project_create.callback(
        pm_cog, interaction, name="Infra", prefix="INF2", role=mock_role, channel=mock_channel
    )
    interaction.followup.send.assert_awaited()
    last_call_arg = interaction.followup.send.call_args[0][0]
    assert "already exists" in last_call_arg

    # 2. Squad Subcommand: Duplicate squad name translates to clean error message
    interaction.followup.send.reset_mock()
    await services["squad"].create_squad(guild_id=guild_id, name="Blue Squad", discord_role_id=5555)
    mock_role = MagicMock(spec=discord.Role)
    mock_role.id = 6666
    await pm_cog.squad_create.callback(pm_cog, interaction, role=mock_role, squad_name="Blue Squad")
    interaction.followup.send.assert_awaited()
    last_call_arg = interaction.followup.send.call_args[0][0]
    assert "already exists" in last_call_arg

    # 3. Task Cog: Status update on non-existent task
    interaction.followup.send.reset_mock()
    await pm_cog.task_status.callback(pm_cog, interaction, task="NON-EXISTENT", status="completed")
    interaction.followup.send.assert_awaited()
    last_call_arg = interaction.followup.send.call_args[0][0]
    assert "not found" in last_call_arg


def test_translate_error_app_command_errors():
    """Verify translate_error correctly formats discord.app_commands errors without unexpected flags."""
    # 1. MissingPermissions
    missing_perm_err = discord.app_commands.MissingPermissions(["manage_guild"])
    msg, unexpected = translate_error(missing_perm_err, "creating project")
    assert not unexpected
    assert "Manage Server" in msg
    assert "missing required Discord permission" in msg

    # 2. BotMissingPermissions
    bot_missing_err = discord.app_commands.BotMissingPermissions(["manage_channels"])
    msg, unexpected = translate_error(bot_missing_err, "creating channel")
    assert not unexpected
    assert "Manage Channels" in msg
    assert "bot is missing required Discord permission" in msg

    # 3. CommandOnCooldown
    cooldown_err = discord.app_commands.CommandOnCooldown(5.0, 10.0)
    msg, unexpected = translate_error(cooldown_err, "syncing commands")
    assert not unexpected
    assert "cooldown" in msg

    # 4. Generic CheckFailure
    check_err = discord.app_commands.CheckFailure()
    msg, unexpected = translate_error(check_err, "executing command")
    assert not unexpected
    assert "do not have permission" in msg

    # 5. CommandInvokeError unwraps original error
    mock_cmd = MagicMock()
    mock_cmd.name = "test_cmd"
    invoke_err = discord.app_commands.CommandInvokeError(mock_cmd, ValueError("Validation problem"))
    msg, unexpected = translate_error(invoke_err, "executing test_cmd")
    assert not unexpected
    assert "Validation problem" in msg


@pytest.mark.asyncio
async def test_cog_app_command_error_handles_missing_permissions(services, caplog):
    """Verify PmCog.cog_app_command_error catches MissingPermissions and responds ephemerally."""
    bot = MagicMock(spec=discord.Client)
    pm_cog = PmCog(
        bot,
        project_service=services["project"],
        squad_service=services["squad"],
        task_service=services["task"],
    )

    interaction = MagicMock(spec=discord.Interaction)
    interaction.command = MagicMock()
    interaction.command.qualified_name = "pm project create"
    interaction.response = MagicMock()
    interaction.response.is_done.return_value = False
    interaction.response.send_message = AsyncMock()

    missing_err = discord.app_commands.MissingPermissions(["manage_guild"])

    with caplog.at_level(logging.WARNING):
        await pm_cog.cog_app_command_error(interaction, missing_err)

    interaction.response.send_message.assert_awaited_once()
    sent_msg = interaction.response.send_message.call_args[0][0]
    assert "Manage Server" in sent_msg
    assert interaction.response.send_message.call_args[1].get("ephemeral") is True
    assert "App command check failure while executing '/pm project create'" in caplog.text


@pytest.mark.asyncio
async def test_bot_on_tree_error_handles_missing_permissions(services, caplog):
    """Verify DggPmBot.on_tree_error catches unhandled command tree errors and responds ephemerally."""
    from src.adapters.discord_bot.bot import DggPmBot

    bot = DggPmBot(
        task_service=services["task"],
        project_service=services["project"],
        squad_service=services["squad"],
    )

    interaction = MagicMock(spec=discord.Interaction)
    interaction.command = MagicMock()
    interaction.command.qualified_name = "pm squad create"
    interaction.response = MagicMock()
    interaction.response.is_done.return_value = False
    interaction.response.send_message = AsyncMock()

    missing_err = discord.app_commands.MissingPermissions(["manage_guild"])

    with caplog.at_level(logging.WARNING):
        await bot.on_tree_error(interaction, missing_err)

    interaction.response.send_message.assert_awaited_once()
    sent_msg = interaction.response.send_message.call_args[0][0]
    assert "Manage Server" in sent_msg
    assert interaction.response.send_message.call_args[1].get("ephemeral") is True
    assert "App command check failure while executing '/pm squad create'" in caplog.text
