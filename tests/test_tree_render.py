import io

import discord
import pytest
from PIL import Image

from src.adapters.rendering import render_tree


def test_render_empty_tree():
    nodes = []
    edges = []

    buf_lr = render_tree(nodes, edges, title="Empty Project", mode="lr")
    assert isinstance(buf_lr, io.BytesIO)
    img_lr = Image.open(buf_lr)
    assert img_lr.width > 0 and img_lr.height > 0
    assert img_lr.format == "PNG"

    buf_tb = render_tree(nodes, edges, title="Empty Project", mode="tb")
    img_tb = Image.open(buf_tb)
    assert img_tb.width > 0 and img_tb.height > 0


def test_render_linear_chain():
    nodes = [
        {
            "key": "LIN-1",
            "short_id": "[LIN-1]",
            "name": "Setup Foundation",
            "description": "",
            "state": "complete",
            "assignee": "Alice",
            "priority": "normal",
        },
        {
            "key": "LIN-2",
            "short_id": "[LIN-2]",
            "name": "Build Architecture",
            "description": "",
            "state": "active",
            "assignee": "Bob",
            "priority": "high",
        },
        {
            "key": "LIN-3",
            "short_id": "[LIN-3]",
            "name": "Deploy Release",
            "description": "",
            "state": "locked",
            "assignee": None,
            "priority": "low",
        },
    ]
    edges = [
        ("LIN-1", "LIN-2"),
        ("LIN-2", "LIN-3"),
    ]

    buf_lr = render_tree(nodes, edges, title="Linear Chain Project", mode="lr")
    img_lr = Image.open(buf_lr)
    assert img_lr.width >= 400
    assert img_lr.height >= 200

    buf_tb = render_tree(nodes, edges, title="Linear Chain Project", mode="tb")
    img_tb = Image.open(buf_tb)
    assert img_tb.width >= 200
    assert img_tb.height >= 400


def test_render_diamond_dag():
    nodes = [
        {
            "key": "DIA-1",
            "short_id": "[DIA-1]",
            "name": "Root Task",
            "description": "",
            "state": "complete",
            "assignee": "Lead",
            "priority": "normal",
        },
        {
            "key": "DIA-2",
            "short_id": "[DIA-2]",
            "name": "Branch Left",
            "description": "",
            "state": "complete",
            "assignee": "Dev A",
            "priority": "normal",
        },
        {
            "key": "DIA-3",
            "short_id": "[DIA-3]",
            "name": "Branch Right",
            "description": "",
            "state": "active",
            "assignee": "Dev B",
            "priority": "high",
        },
        {
            "key": "DIA-4",
            "short_id": "[DIA-4]",
            "name": "Convergence Merge",
            "description": "",
            "state": "locked",
            "assignee": None,
            "priority": "low",
        },
    ]
    edges = [
        ("DIA-1", "DIA-2"),
        ("DIA-1", "DIA-3"),
        ("DIA-2", "DIA-4"),
        ("DIA-3", "DIA-4"),
    ]

    buf = render_tree(nodes, edges, title="Diamond DAG Project", mode="lr")
    img = Image.open(buf)
    assert img.size[0] > 400 and img.size[1] > 200


def test_render_vertical_mode_wide_layer():
    # A 3-node layer in vertical (tb) mode must have canvas width fitting all cards without clipping
    nodes = [
        {
            "key": f"W-{i}",
            "short_id": f"[W-{i}]",
            "name": f"Task {i}",
            "description": "",
            "state": "available",
            "assignee": None,
            "priority": "normal",
        }
        for i in range(1, 4)
    ]
    edges = []  # all in layer 0 (side by side in tb mode)

    buf_tb = render_tree(nodes, edges, title="Wide Vertical Tree", mode="tb")
    img_tb = Image.open(buf_tb)
    # 3 * 320 + 2 * 36 + 2 * 48 = 1128
    assert img_tb.width >= 1128


@pytest.mark.asyncio
async def test_service_render_project_tree_integration(services):
    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 998877667

    project = await proj_srv.create_project(guild_id=guild_id, name="Visualizer App", prefix="VIS")
    t1 = await task_srv.create_task(
        guild_id=guild_id, title="Visual Core", creator_discord_id=1001, project_id=project.id
    )
    await task_srv.create_task(
        guild_id=guild_id,
        title="Canvas Render",
        creator_discord_id=1001,
        project_id=project.id,
        prerequisite_short_ids=[t1.short_id],
    )

    buf_lr = await task_srv.render_project_tree(guild_id=guild_id, project_id=project.id, orientation="lr")
    assert isinstance(buf_lr, io.BytesIO)
    img_lr = Image.open(buf_lr)
    assert img_lr.format == "PNG"

    buf_tb = await task_srv.render_project_tree(guild_id=guild_id, project_id=project.id, orientation="tb")
    assert isinstance(buf_tb, io.BytesIO)
    img_tb = Image.open(buf_tb)
    assert img_tb.format == "PNG"


@pytest.mark.asyncio
async def test_render_project_tree_assignee_name_resolution(services):
    from unittest.mock import MagicMock

    from src.services.task_service import resolve_member_name

    proj_srv = services["project"]
    task_srv = services["task"]
    guild_id = 998877668
    user_id = 12472731781680

    # 1. Test resolve_member_name helper
    assert resolve_member_name(None, None) is None
    assert resolve_member_name(user_id, {user_id: "alice_dev"}) == "alice_dev"
    assert resolve_member_name(user_id, lambda uid: f"Dev-{uid}") == f"Dev-{user_id}"

    mock_member = MagicMock(spec=discord.Member)
    mock_member.display_name = "PJ_Lead"
    mock_guild = MagicMock(spec=discord.Guild)
    mock_guild.get_member.return_value = mock_member
    assert resolve_member_name(user_id, mock_guild) == "PJ_Lead"

    # 2. Test get_project_tree_data with member_resolver
    project = await proj_srv.create_project(guild_id=guild_id, name="Assignee Test", prefix="ASG")
    await task_srv.create_task(
        guild_id=guild_id,
        title="Assigned Task",
        creator_discord_id=1001,
        assignee_discord_id=user_id,
        project_id=project.id,
    )

    # Without resolver: defaults to "User <id>"
    nodes_raw, _ = await task_srv.get_project_tree_data(guild_id, project.id)
    assert nodes_raw[0]["assignee"] == f"User {user_id}"

    # With resolver: resolves to human-readable username
    nodes_resolved, _ = await task_srv.get_project_tree_data(guild_id, project.id, member_resolver=mock_guild)
    assert nodes_resolved[0]["assignee"] == "PJ_Lead"

    # Render tree with member_resolver
    buf = await task_srv.render_project_tree(
        guild_id=guild_id,
        project_id=project.id,
        orientation="lr",
        member_resolver=mock_guild,
    )
    assert isinstance(buf, io.BytesIO)
    img = Image.open(buf)
    assert img.format == "PNG"
