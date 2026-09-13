from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from src.adapters.discord_bot.cogs.pm_cog import PmCog
from src.adapters.discord_bot.views.task_builder import DraftPrerequisiteSelectView, TaskCreateDraftView
from src.adapters.discord_bot.views.task_dependency_view import TaskDependencyView, build_dependency_embed
from src.adapters.discord_bot.views.tree_view import TechTreeProjectSelectView, TechTreeViewer


@pytest.mark.asyncio
async def test_task_dependency_view_callbacks(services):
    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 998877668

    project = await proj_srv.create_project(guild_id=guild_id, name="Infra Tree", prefix="INF")
    t1 = await task_srv.create_task(
        guild_id=guild_id, title="Foundation", creator_discord_id=1001, project_id=project.id
    )
    t2 = await task_srv.create_task(
        guild_id=guild_id, title="App Engine", creator_discord_id=1001, project_id=project.id
    )

    # Initial view
    view = TaskDependencyView(
        task_service=task_srv,
        task=t2,
        sibling_tasks=[t1, t2],
        prerequisites=[],
        dependents=[],
    )
    embed = build_dependency_embed(t2, [], [])
    assert "INF-2" in embed.title
    assert len(view.children) >= 1

    # Select t1 as prerequisite
    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    interaction.user = MagicMock()
    interaction.user.id = 1001
    interaction.data = {"values": [t1.short_id]}
    interaction.response = MagicMock()
    interaction.response.edit_message = AsyncMock()

    await view._on_select_prerequisites(interaction)
    interaction.response.edit_message.assert_awaited_once()

    prereqs, _ = await task_srv.get_task_dependencies(t2.id)
    assert len(prereqs) == 1
    assert prereqs[0].id == t1.id


@pytest.mark.asyncio
async def test_task_builder_draft_prerequisite_selection(services):
    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 998877669

    project = await proj_srv.create_project(guild_id=guild_id, name="Mobile Hub", prefix="MOB")
    t1 = await task_srv.create_task(
        guild_id=guild_id, title="Login Screen", creator_discord_id=1001, project_id=project.id
    )

    draft_view = TaskCreateDraftView(
        task_service=task_srv,
        project=project,
        title="Settings Screen",
        description="Profile and theme preferences",
    )
    assert draft_view.prereqs_btn is not None

    select_view = DraftPrerequisiteSelectView(draft_view, [t1])
    interaction = MagicMock(spec=discord.Interaction)
    interaction.data = {"values": [t1.short_id]}
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.response.edit_message = AsyncMock()

    await select_view._on_select(interaction)
    assert draft_view.prerequisite_short_ids == [t1.short_id]

    await select_view._on_done(interaction)
    assert interaction.response.edit_message.await_count == 2


@pytest.mark.asyncio
async def test_tree_viewer_and_select_views(services):
    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 998877670

    project = await proj_srv.create_project(guild_id=guild_id, name="Analytics Core", prefix="ANA")
    await task_srv.create_task(guild_id=guild_id, title="ETL Pipeline", creator_discord_id=1001, project_id=project.id)

    # TechTreeViewer
    viewer = TechTreeViewer(task_srv, project, current_orientation="lr")
    assert len(viewer.children) == 2

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.edit_original_response = AsyncMock()

    # Toggle to vertical
    await viewer._on_tb_clicked(interaction)
    assert viewer.current_orientation == "tb"
    interaction.edit_original_response.assert_awaited_once()

    # TechTreeProjectSelectView
    proj_select = TechTreeProjectSelectView(task_srv, proj_srv, [project], orientation="lr")
    interaction.data = {"values": [str(project.id)]}
    interaction.edit_original_response.reset_mock()
    await proj_select._on_select_project(interaction)
    interaction.edit_original_response.assert_awaited_once()


@pytest.mark.asyncio
async def test_dependency_and_tree_cogs_slash_commands(services):
    proj_srv = services["project"]
    task_srv = services["task"]
    squad_srv = services["squad"]
    guild_id = 998877671

    project = await proj_srv.create_project(guild_id=guild_id, name="Security Audit", prefix="SEC")
    t1 = await task_srv.create_task(
        guild_id=guild_id, title="Reconnaissance", creator_discord_id=1001, project_id=project.id
    )
    t2 = await task_srv.create_task(
        guild_id=guild_id, title="Exploitation", creator_discord_id=1001, project_id=project.id
    )

    bot = MagicMock()
    pm_cog = PmCog(bot=bot, project_service=proj_srv, squad_service=squad_srv, task_service=task_srv)

    interaction = MagicMock(spec=discord.Interaction)
    interaction.guild = MagicMock()
    interaction.guild.id = guild_id
    interaction.user = MagicMock()
    interaction.user.id = 1001
    interaction.response = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()

    # 1. /pm task depend
    await pm_cog.task_depend.callback(pm_cog, interaction=interaction, task=t2.short_id, depends_on=t1.short_id)
    interaction.followup.send.assert_awaited_once()
    assert "Linked dependency" in interaction.followup.send.call_args.args[0]

    # 2. /pm task undepend
    interaction.followup.send.reset_mock()
    await pm_cog.task_undepend.callback(pm_cog, interaction=interaction, task=t2.short_id, depends_on=t1.short_id)
    interaction.followup.send.assert_awaited_once()
    assert "Unlinked dependency" in interaction.followup.send.call_args.args[0]

    # 3. /pm project tree
    interaction.followup.send.reset_mock()
    await pm_cog.project_tree.callback(pm_cog, interaction=interaction, project_name="Security Audit", orientation=None)
    interaction.followup.send.assert_awaited_once()
    embed = interaction.followup.send.call_args.kwargs.get("embed")
    assert embed is not None
    assert "Tech Tree" in embed.title

    # 4. /pm tree
    interaction.followup.send.reset_mock()
    await pm_cog.pm_tree.callback(pm_cog, interaction=interaction, project_name="Security Audit", orientation=None)
    interaction.followup.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_task_blocked_confirm_view_and_embed(services):
    """TaskBlockedConfirmView renders unresolved blockers, supports proceed (bypass) and cancel."""
    from src.adapters.discord_bot.views.task_blocked_view import (
        TaskBlockedConfirmView,
        build_task_blocked_confirm_embed,
    )
    from src.domain.enums import TaskStatus

    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 998877671

    project = await proj_srv.create_project(guild_id=guild_id, name="Guard Project", prefix="GRD")
    t1 = await task_srv.create_task(
        guild_id=guild_id, title="Prerequisite Task", creator_discord_id=1001, project_id=project.id
    )
    t2 = await task_srv.create_task(
        guild_id=guild_id,
        title="Dependent Task",
        creator_discord_id=1001,
        project_id=project.id,
        prerequisite_short_ids=[t1.short_id],
    )

    # 1. Embed content verification
    embed = build_task_blocked_confirm_embed(t2, TaskStatus.IN_PROGRESS, [t1])
    assert "Unresolved Dependencies" in embed.title
    assert t2.short_id in embed.title
    assert "Unresolved Blockers" in embed.fields[0].name
    assert t1.short_id in embed.fields[0].value

    # 2. View initialization
    mock_workspace = MagicMock()
    mock_workspace.refresh_action_card = AsyncMock()
    mock_workspace.sync_workspace = AsyncMock()

    view = TaskBlockedConfirmView(
        task=t2,
        target_status=TaskStatus.IN_PROGRESS,
        incomplete_prereqs=[t1],
        author_id=1001,
        task_service=task_srv,
        workspace=mock_workspace,
    )

    # Interaction check: unauthorized user rejected
    bad_interaction = MagicMock(spec=discord.Interaction)
    bad_interaction.user = MagicMock(id=9999)
    bad_interaction.response = MagicMock()
    bad_interaction.response.send_message = AsyncMock()
    assert await view.interaction_check(bad_interaction) is False
    bad_interaction.response.send_message.assert_awaited_once()

    # Cancel button preserves NOT_STARTED status
    cancel_interaction = MagicMock(spec=discord.Interaction)
    cancel_interaction.user = MagicMock(id=1001)
    cancel_interaction.response = MagicMock()
    cancel_interaction.response.edit_message = AsyncMock()

    await view.cancel_transition.callback(cancel_interaction)
    cancel_interaction.response.edit_message.assert_awaited_once()
    assert "cancelled" in cancel_interaction.response.edit_message.call_args.kwargs["content"]

    t2_after_cancel = await task_srv.get_by_id(t2.id)
    assert t2_after_cancel.status == TaskStatus.NOT_STARTED

    # Proceed button transitions to IN_PROGRESS
    proceed_interaction = MagicMock(spec=discord.Interaction)
    proceed_interaction.user = MagicMock(id=1001)
    proceed_interaction.response = MagicMock()
    proceed_interaction.response.edit_message = AsyncMock()

    await view.confirm_proceed.callback(proceed_interaction)
    proceed_interaction.response.edit_message.assert_awaited_once()
    assert "Bypassed" in proceed_interaction.response.edit_message.call_args.kwargs["content"]

    t2_after_proceed = await task_srv.get_by_id(t2.id)
    assert t2_after_proceed.status == TaskStatus.IN_PROGRESS
    mock_workspace.refresh_action_card.assert_awaited_once()
    mock_workspace.sync_workspace.assert_awaited_once()
