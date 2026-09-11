from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from src.adapters.discord_bot.views.project_menu import (
    ProjectAssignSquadView,
)
from src.adapters.discord_bot.views.squad_menu import (
    SquadMemberAssignView,
    SquadOverviewListView,
    SquadRosterDetailView,
    build_squad_menu_embed,
)
from src.domain.enums import TeamRoleType


@pytest.mark.asyncio
async def test_scale_30_squad_members_roster_pagination(services):
    """Test that a squad with 30 members properly paginates across 2 pages and enforces character limits."""
    team_srv = services["team"]
    guild_id = 999111222

    # 1. Create a squad
    squad = await team_srv.create_team(guild_id=guild_id, name="Core Infra Squad", discord_role_id=888001)

    # 2. Add 2 Squad Leads and 28 Regular Squad Members (30 total)
    await team_srv.add_team_lead(squad.id, user_discord_id=1001)
    await team_srv.add_team_lead(squad.id, user_discord_id=1002)
    for i in range(1, 29):
        await team_srv.assign_member(squad.id, user_discord_id=2000 + i, role_type=TeamRoleType.MEMBER)

    members = await team_srv.list_members(squad.id)
    assert len(members) == 30

    # 3. Mount SquadRosterDetailView
    view = SquadRosterDetailView(teams=[squad], team_service=team_srv)
    # Before squad selection: only Select + Back
    assert len(view.children) == 2

    # Simulate selecting the squad from the dropdown
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.edit_message = AsyncMock()
    view.select._values = [str(squad.id)]

    await view._on_select(interaction)
    interaction.response.edit_message.assert_awaited_once()

    embed: discord.Embed = interaction.response.edit_message.call_args[1]["embed"]
    assert "Squad Roster: Core Infra Squad" in embed.title
    assert "**Total Members:** `30`" in embed.description
    assert "**Member Page:** `1/2`" in embed.description

    # Assert character limit safety: every field value must be <= 1024
    for field in embed.fields:
        assert len(field.value) <= 1024, f"Field '{field.name}' exceeded 1024 chars!"
    assert len(embed) <= 6000

    # 4. Assert pagination controls are mounted
    assert hasattr(view, "prev_member_btn")
    assert hasattr(view, "next_member_btn")
    assert hasattr(view, "search_member_btn")
    assert view.prev_member_btn.disabled is True
    assert view.next_member_btn.disabled is False

    # 5. Advance to page 2
    interaction.response.edit_message.reset_mock()
    await view._on_next_members_clicked(interaction)
    interaction.response.edit_message.assert_awaited_once()

    page2_embed: discord.Embed = interaction.response.edit_message.call_args[1]["embed"]
    assert "**Member Page:** `2/2`" in page2_embed.description
    assert view.member_page == 1
    assert view.prev_member_btn.disabled is False
    assert view.next_member_btn.disabled is True
    for field in page2_embed.fields:
        assert len(field.value) <= 1024

    # 6. Return to page 1
    interaction.response.edit_message.reset_mock()
    await view._on_prev_members_clicked(interaction)
    assert view.member_page == 0
    assert view.prev_member_btn.disabled is True
    assert view.next_member_btn.disabled is False


@pytest.mark.asyncio
async def test_scale_50_squad_members_prevents_1024_embed_overflow(services):
    """Test that a squad with 50 members (which would exceed 1024 chars unpaginated) safely chunks."""
    team_srv = services["team"]
    guild_id = 999333444

    squad = await team_srv.create_team(guild_id=guild_id, name="Platform Engineering", discord_role_id=888002)

    # Add 50 regular members
    for i in range(1, 51):
        await team_srv.assign_member(squad.id, user_discord_id=3000 + i, role_type=TeamRoleType.MEMBER)

    view = SquadRosterDetailView(teams=[squad], team_service=team_srv)
    view.select._values = [str(squad.id)]

    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.edit_message = AsyncMock()

    await view._on_select(interaction)
    embed: discord.Embed = interaction.response.edit_message.call_args[1]["embed"]

    # 50 members / 20 per page = 3 pages
    assert "**Total Members:** `50`" in embed.description
    assert "**Member Page:** `1/3`" in embed.description

    # Walk all pages and assert strict Discord API payload compliance
    for expected_page in range(3):
        current_embed: discord.Embed = interaction.response.edit_message.call_args[1]["embed"]
        assert f"**Member Page:** `{expected_page + 1}/3`" in current_embed.description
        for field in current_embed.fields:
            assert len(field.value) <= 1024, f"Field '{field.name}' value exceeded 1024 chars on page {expected_page}!"
        assert len(current_embed.fields) <= 25
        assert len(current_embed) <= 6000

        if expected_page < 2:
            interaction.response.edit_message.reset_mock()
            await view._on_next_members_clicked(interaction)


@pytest.mark.asyncio
async def test_scale_100_squad_members_and_search_modal(services):
    """Test extreme scale (100 members across 5 pages) and in-roster member searching."""
    team_srv = services["team"]
    guild_id = 999555666

    squad = await team_srv.create_team(guild_id=guild_id, name="Massive Contributor Squad", discord_role_id=888003)

    # Seed 100 members
    target_search_id = 9500000095
    for i in range(1, 101):
        uid = target_search_id if i == 95 else (4000 + i)
        await team_srv.assign_member(squad.id, user_discord_id=uid, role_type=TeamRoleType.MEMBER)

    view = SquadRosterDetailView(teams=[squad], team_service=team_srv)
    view.select._values = [str(squad.id)]

    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.edit_message = AsyncMock()

    await view._on_select(interaction)
    embed: discord.Embed = interaction.response.edit_message.call_args[1]["embed"]

    assert "**Total Members:** `100`" in embed.description
    assert "**Member Page:** `1/5`" in embed.description

    # Test member search modal submission
    interaction.response.edit_message.reset_mock()
    await view._apply_member_search(interaction, query=str(target_search_id))
    interaction.response.edit_message.assert_awaited_once()

    filtered_embed: discord.Embed = interaction.response.edit_message.call_args[1]["embed"]
    assert f"Member Search:** `{target_search_id}`" in filtered_embed.description
    assert f"<@{target_search_id}>" in filtered_embed.fields[1].value
    assert "Verified Members (1)" in filtered_embed.fields[1].name
    assert hasattr(view, "clear_member_btn")

    # Clear filter
    interaction.response.edit_message.reset_mock()
    await view._on_clear_member_filter_clicked(interaction)
    cleared_embed: discord.Embed = interaction.response.edit_message.call_args[1]["embed"]
    assert "**Member Page:** `1/5`" in cleared_embed.description
    assert view.member_query == ""


@pytest.mark.asyncio
async def test_scale_30_squads_dropdown_and_overview_pagination(services):
    """Test scaling server squads to 30 (>25 limit) across SquadOverviewListView and SquadMemberAssignView."""
    team_srv = services["team"]
    proj_srv = services["project"]
    guild_id = 999777888

    # Create 30 squads
    squads = []
    for i in range(1, 31):
        s = await team_srv.create_team(guild_id=guild_id, name=f"Squad {i:02d}", discord_role_id=900000 + i)
        squads.append(s)

    assert len(squads) == 30

    # 1. Test SquadOverviewListView pagination (10 per page = 3 pages)
    overview_view = SquadOverviewListView(teams=squads, team_service=team_srv)
    embed = await overview_view.build_embed()

    assert "Server Squads (30) (Page 1/3)" in embed.title
    assert len(embed.fields) == 10  # Strictly <= 25 fields
    assert overview_view.prev_btn.disabled is True
    assert overview_view.next_btn.disabled is False

    # Page 2
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.edit_message = AsyncMock()

    await overview_view._on_next_clicked(interaction)
    page2_embed: discord.Embed = interaction.response.edit_message.call_args[1]["embed"]
    assert "Page 2/3" in page2_embed.title
    assert len(page2_embed.fields) == 10

    # 2. Test SquadMemberAssignView (25-item select dropdown limit)
    assign_view = SquadMemberAssignView(teams=squads, team_service=team_srv)
    # Page 0 has exactly 25 select options
    assert len(assign_view.team_select.options) == 25
    assert "Page 1/2" in assign_view.team_select.placeholder
    assert hasattr(assign_view, "prev_btn")
    assert hasattr(assign_view, "next_btn")
    assert assign_view.prev_btn.disabled is True
    assert assign_view.next_btn.disabled is False

    # Navigate to page 2 (5 squads)
    await assign_view._on_next_clicked(interaction)
    assert len(assign_view.team_select.options) == 5
    assert "Page 2/2" in assign_view.team_select.placeholder
    assert assign_view.next_btn.disabled is True

    # Test squad search in assignment view
    await assign_view._apply_squad_search(interaction, query="Squad 29")
    assert len(assign_view.team_select.options) == 1
    assert assign_view.team_select.options[0].label == "Squad 29"

    # 3. Test ProjectAssignSquadView pagination with 30 squads
    project = await proj_srv.create_project(guild_id=guild_id, name="Scale Project", prefix="SCL")
    proj_assign_view = ProjectAssignSquadView(
        projects=[project],
        teams=squads,
        project_service=proj_srv,
        team_service=team_srv,
    )
    # Page 0 has 25 squad options
    assert len(proj_assign_view.team_select.options) == 25
    assert hasattr(proj_assign_view, "prev_team_btn")
    assert hasattr(proj_assign_view, "next_team_btn")
    assert proj_assign_view.prev_team_btn.disabled is True

    # Advance to page 2
    await proj_assign_view._on_next_team_clicked(interaction)
    assert len(proj_assign_view.team_select.options) == 5
    assert proj_assign_view.next_team_btn.disabled is True


@pytest.mark.asyncio
async def test_canonical_squad_views_and_embeds():
    """Test canonical domain terminology (Squad) across views and embed copy."""
    # Embed copy verification
    embed = build_squad_menu_embed(can_create_squads=True, can_assign_members=True)
    assert "Squad Management Hub" in embed.title
    assert "Create Squad" in embed.description
    assert "Assign Member" in embed.description
    assert "Squad Roster" in embed.description
    assert "Contributor Squads & Permission Mappings" in embed.description

    # Embed for members without elevated creation permissions
    member_embed = build_squad_menu_embed(can_create_squads=False, can_assign_members=False)
    assert "Server Squad Rosters" in member_embed.description
    assert "Create Squad" not in member_embed.description
