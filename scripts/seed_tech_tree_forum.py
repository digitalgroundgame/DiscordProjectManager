"""Tech-Tree Forum Seeding Script for dgg-pm.

Creates a specialized test project forum equipped with:
1. A multi-tier Directed Acyclic Graph (DAG) tech-tree of tasks across various states
   (Completed, In Progress, Available/Ready, Locked, and Blocked).
2. Explicit database task dependencies in the `task_dependencies` table.
3. A Discord Forum channel with status & priority tags.
4. A pinned Tech Tree Control Center forum post with an attached Civilization-style
   rendered PNG dependency graph and interactive Horizontal ↔ Vertical view toggles.
5. Dedicated interactive forum threads for every task card with TaskActionView buttons.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# Add project root to sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import discord  # noqa: E402
from sqlalchemy import delete, select  # noqa: E402

from src.adapters.db.postgres_repo import (  # noqa: E402
    PostgresOutboxRepo,
    PostgresProjectRepo,
    PostgresTaskRepo,
)
from src.adapters.db.session import async_session_factory, close_db, init_db  # noqa: E402
from src.adapters.db.tables import ProjectTable, TaskDependencyTable, TaskTable  # noqa: E402
from src.adapters.db.unit_of_work import SqlAlchemyUnitOfWork  # noqa: E402
from src.adapters.discord_bot.member_resolver import resolve_discord_member_name  # noqa: E402
from src.adapters.discord_bot.views.forum_helpers import (  # noqa: E402
    STANDARD_PM_TAG_DEFINITIONS,
    resolve_forum_tags,
)
from src.adapters.discord_bot.views.task_buttons import TaskActionView  # noqa: E402
from src.adapters.discord_bot.views.task_embed import (  # noqa: E402
    build_task_embed,
    build_thread_workspace_content,
)
from src.adapters.discord_bot.views.tree_view import TechTreeViewer  # noqa: E402
from src.config import settings  # noqa: E402
from src.domain.enums import PriorityLevel, TaskStatus  # noqa: E402
from src.domain.models import Project, Task  # noqa: E402
from src.services.outbox_service import OutboxService  # noqa: E402
from src.services.project_service import ProjectService  # noqa: E402
from src.services.task_service import TaskService  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("seed_tech_tree_forum")

# Definition of the tech tree tasks network
# (Index, Title, Description, Priority, Status, BlockedFlag, PrereqIndices, DaysOffset)
TECH_TREE_NODES: list[dict[str, Any]] = [
    # --- Tier 0: Foundations (Completed) ---
    {
        "index": 1,
        "title": "Base Kernel Architecture",
        "description": "Core microkernel, memory allocator, and system bus.\nUnlocks foundational subsystems.",
        "priority": PriorityLevel.HIGH,
        "status": TaskStatus.COMPLETED,
        "blocked": False,
        "prereq_indices": [],
        "days_offset": None,
    },
    {
        "index": 2,
        "title": "Distributed Storage Engine",
        "description": "Persistent LSM-tree storage engine and write-ahead log (WAL) synchronization.",
        "priority": PriorityLevel.HIGH,
        "status": TaskStatus.COMPLETED,
        "blocked": False,
        "prereq_indices": [],
        "days_offset": None,
    },
    # --- Tier 1: Core Subsystems ---
    {
        "index": 3,
        "title": "Zero-Knowledge Auth Subsystem",
        "description": "Elliptic curve cryptography, identity token issuance, and session revocation.",
        "priority": PriorityLevel.HIGH,
        "status": TaskStatus.COMPLETED,
        "blocked": False,
        "prereq_indices": [1],
        "days_offset": None,
    },
    {
        "index": 4,
        "title": "P2P Gossip Network Protocol",
        "description": "Kademlia DHT routing table, peer discovery, and NAT traversal protocol.",
        "priority": PriorityLevel.HIGH,
        "status": TaskStatus.IN_PROGRESS,
        "blocked": False,
        "prereq_indices": [1],
        "days_offset": 2,
    },
    {
        "index": 5,
        "title": "Distributed Query Optimizer",
        "description": "Vectorized columnar execution engine and partitioned aggregation pipeline.",
        "priority": PriorityLevel.NORMAL,
        "status": TaskStatus.COMPLETED,
        "blocked": False,
        "prereq_indices": [2],
        "days_offset": None,
    },
    # --- Tier 2: Coordination & Caching ---
    {
        "index": 6,
        "title": "Real-time Sync & Consensus Mesh",
        "description": "Raft consensus state machine and high-throughput event broadcast mesh.",
        "priority": PriorityLevel.HIGH,
        "status": TaskStatus.IN_PROGRESS,
        "blocked": False,
        "prereq_indices": [3, 4],
        "days_offset": 4,
    },
    {
        "index": 7,
        "title": "Streaming Analytics Pipeline",
        "description": "Real-time sliding window aggregations and metric stream publisher (Ready to start!).",
        "priority": PriorityLevel.NORMAL,
        "status": TaskStatus.NOT_STARTED,
        "blocked": False,
        "prereq_indices": [5],
        "days_offset": 7,
    },
    {
        "index": 8,
        "title": "Edge Cache & Memory Tier",
        "description": "Distributed Redis-compatible tiered caching layer with smart invalidation.",
        "priority": PriorityLevel.NORMAL,
        "status": TaskStatus.COMPLETED,
        "blocked": False,
        "prereq_indices": [5],
        "days_offset": None,
    },
    # --- Tier 3: Applications & Endpoints ---
    {
        "index": 9,
        "title": "Neural Decision Engine",
        "description": "Autonomous agent policy evaluator and reinforcement learning runtime (Locked).",
        "priority": PriorityLevel.HIGH,
        "status": TaskStatus.NOT_STARTED,
        "blocked": False,
        "prereq_indices": [6, 7],
        "days_offset": 10,
    },
    {
        "index": 10,
        "title": "Public Gateway & SDK APIs",
        "description": "OpenAPI documentation, GraphQL gateway, and rate-limited developer endpoints.",
        "priority": PriorityLevel.NORMAL,
        "status": TaskStatus.NOT_STARTED,
        "blocked": False,
        "prereq_indices": [8],
        "days_offset": 6,
    },
    {
        "index": 11,
        "title": "High-Frequency Telemetry Ingestion",
        "description": "Sub-millisecond packet ingest buffer. Currently blocked pending hardware test benchmarks.",
        "priority": PriorityLevel.NORMAL,
        "status": TaskStatus.NOT_STARTED,
        "blocked": True,
        "prereq_indices": [8],
        "days_offset": 5,
    },
    # --- Tier 4: Endgame Milestone ---
    {
        "index": 12,
        "title": "Autonomous Swarm Orchestrator",
        "description": "Endgame milestone: Full autonomous decentralized swarm fleet coordination.",
        "priority": PriorityLevel.HIGH,
        "status": TaskStatus.NOT_STARTED,
        "blocked": False,
        "prereq_indices": [9, 10],
        "days_offset": 14,
    },
]


def check_production_safety_guard() -> None:
    """Strictly prevents test seeding in production environments."""
    env = (settings.ENVIRONMENT or "").lower()
    if env in ("prod", "production"):
        raise RuntimeError(f"⛔ SAFETY BLOCK: Seeding script cannot be run in production (ENVIRONMENT='{env}').")

    db_url = (settings.DATABASE_URL or "").lower()
    unsafe_keywords = [
        "rds.amazonaws.com",
        "supabase.co",
        "neon.tech",
        "cockroachlabs.cloud",
        "render.com",
        "railway.app",
        "cloudsql",
    ]
    if any(kw in db_url for kw in unsafe_keywords):
        raise RuntimeError(
            f"⛔ SAFETY BLOCK: DATABASE_URL appears to point to a production/cloud database ({db_url})! "
            "Seeding aborted to prevent accidental data modification."
        )


async def seed_tech_tree_db(
    guild_id: int,
    project_name: str = "Autonomous Swarm Platform",
    project_prefix: str = "TREE",
    project_desc: str = "Decentralized autonomous swarm intelligence project with comprehensive DAG tech-tree",
    reset_existing: bool = False,
    actor_discord_id: int | None = None,
    session_factory: Any | None = None,
) -> tuple[Project, dict[int, Task], TaskService]:
    """Seeds the tech tree project, tasks, and dependency edges in PostgreSQL."""
    if session_factory is None:
        await init_db()
        sess_factory = async_session_factory
    else:
        sess_factory = session_factory

    task_repo = PostgresTaskRepo(sess_factory)
    project_repo = PostgresProjectRepo(sess_factory)
    outbox_repo = PostgresOutboxRepo(sess_factory)
    uow = SqlAlchemyUnitOfWork(sess_factory)
    project_service = ProjectService(project_repo)
    outbox_service = OutboxService(outbox_repo)
    task_service = TaskService(task_repo, project_service, outbox_service, uow=uow)

    actor_id = actor_discord_id or int(settings.DISCORD_CLIENT_ID or 1001)

    # 1. Check or create project
    existing_project = await project_repo.get_by_prefix(guild_id, project_prefix)
    if existing_project and reset_existing:
        logger.info(f"Purging existing project [{project_prefix}] '{existing_project.name}' for fresh reseed...")
        async with sess_factory() as session:
            # Delete dependencies and tasks for this project
            task_ids_stmt = select(TaskTable.id).where(TaskTable.project_id == existing_project.id)
            res = await session.execute(task_ids_stmt)
            t_ids = [r[0] for r in res.all()]
            if t_ids:
                await session.execute(
                    delete(TaskDependencyTable).where(
                        (TaskDependencyTable.task_id.in_(t_ids)) | (TaskDependencyTable.depends_on_task_id.in_(t_ids))
                    )
                )
                await session.execute(delete(TaskTable).where(TaskTable.id.in_(t_ids)))
            await session.execute(delete(ProjectTable).where(ProjectTable.id == existing_project.id))
            await session.commit()
        existing_project = None

    if not existing_project:
        project = await project_service.create_project(
            guild_id=guild_id,
            name=project_name,
            prefix=project_prefix,
            description=project_desc,
            category="AI & Robotics",
        )
        logger.info(f"Created project [{project.prefix}] '{project.name}' (ID: {project.id})")
    else:
        project = existing_project
        logger.info(f"Using existing project [{project.prefix}] '{project.name}' (ID: {project.id})")

    # 2. Query existing tasks in project
    existing_tasks, _ = await task_service.list_tasks(guild_id, project_id=project.id, limit=100)
    existing_by_title = {t.title.lower(): t for t in existing_tasks}

    # 3. Create all tasks
    tasks_by_index: dict[int, Task] = {}
    for node in TECH_TREE_NODES:
        idx = node["index"]
        title = node["title"]
        desc = node["description"]
        priority = node["priority"]
        status = node["status"]
        blocked = node["blocked"]
        days_offset = node["days_offset"]

        due_at = (datetime.now(UTC) + timedelta(days=days_offset)) if days_offset is not None else None
        meta = {"blocked": True} if blocked else {}

        task = existing_by_title.get(title.lower())
        if not task:
            task = await task_service.create_task(
                guild_id=guild_id,
                title=title,
                creator_discord_id=actor_id,
                project_id=project.id,
                body=desc,
                priority=priority,
                due_at=due_at,
                metadata_json=meta,
            )
            logger.info(f"  Created Task [{task.short_id}] '{task.title}'")
        else:
            if blocked and not task.metadata_json.get("blocked"):
                await task_service.update_task_details(task.id, metadata_json=meta)

        if status != TaskStatus.NOT_STARTED and task.status != status:
            task = await task_service.update_status(
                task_id=task.id,
                new_status=status,
                expected_version=task.version,
                actor_discord_id=actor_id,
            )

        tasks_by_index[idx] = task

    # 4. Link dependencies in DAG
    logger.info("Linking DAG dependency edges in database...")
    for node in TECH_TREE_NODES:
        idx = node["index"]
        task = tasks_by_index[idx]
        for prereq_idx in node["prereq_indices"]:
            prereq_task = tasks_by_index[prereq_idx]
            try:
                await task_service.add_dependency(
                    guild_id=guild_id,
                    task_short_id=task.short_id,
                    depends_on_short_id=prereq_task.short_id,
                    actor_discord_id=actor_id,
                )
                logger.info(f"  🔗 Linked [{task.short_id}] depends on -> [{prereq_task.short_id}]")
            except Exception as e:
                logger.debug(f"  (Dependency edge [{task.short_id}] -> [{prereq_task.short_id}] already exists: {e})")

    return project, tasks_by_index, task_service


async def seed_discord_forum(
    guild_id: int,
    project: Project,
    tasks_by_index: dict[int, Task],
    task_service: TaskService,
    channel_name: str = "🌲-tech-tree-demo",
    reset_existing: bool = False,
) -> None:
    """Creates the Discord Forum channel, pinned tech tree graph hub post, and task cards."""
    if not settings.DISCORD_BOT_TOKEN:
        logger.warning("DISCORD_BOT_TOKEN not configured. Skipping Discord forum posting.")
        return

    client = discord.Client(intents=discord.Intents.default())
    await client.login(settings.DISCORD_BOT_TOKEN)
    bot_user_id = client.user.id if client.user else int(settings.DISCORD_CLIENT_ID or 1001)
    logger.info(f"Authenticated with Discord as Bot ID {bot_user_id}")

    try:
        guild = await client.fetch_guild(guild_id)
        channels = await guild.fetch_channels()
    except Exception as e:
        logger.error(f"Failed to fetch Discord guild/channels: {e}")
        await client.close()
        return

    logger.info(f"Connected to Discord Guild: '{guild.name}' (Found {len(channels)} channels)")

    # 1. Find or create Projects Category
    category_name = "📁 DGG-PM Projects"
    category = next(
        (c for c in channels if isinstance(c, discord.CategoryChannel) and c.name.lower() == category_name.lower()),
        None,
    )
    if not category:
        try:
            category = await guild.create_category(category_name)
            logger.info(f"Created Discord Category '{category.name}'")
        except Exception as e:
            logger.warning(f"Could not create Discord category: {e}")

    # 2. Define custom forum tags including Tech-Tree & Blocked
    custom_tags = [discord.ForumTag(name=d["name"], emoji=d["emoji"]) for d in STANDARD_PM_TAG_DEFINITIONS]
    custom_tags.extend(
        [
            discord.ForumTag(name="Tech-Tree", emoji="🌲"),
            discord.ForumTag(name="Blocked", emoji="⛔"),
        ]
    )

    # 3. Find or create Forum Channel
    forum_channel = next(
        (
            c
            for c in channels
            if isinstance(c, discord.ForumChannel)
            and c.name.lower() in (channel_name.lower(), channel_name.replace("🌲-", "").lower())
        ),
        None,
    )

    if not forum_channel:
        try:
            forum_channel = await guild.create_forum(
                name=channel_name,
                category=category,
                topic=f"🌲 Interactive Tech Tree & Dependency Graph for [{project.prefix}] {project.name}",
                available_tags=custom_tags,
            )
            logger.info(f"Created Discord Forum Channel: #{forum_channel.name} (ID: {forum_channel.id})")
        except Exception as e:
            logger.error(f"Failed to create forum channel #{channel_name}: {e}")
            await client.close()
            return
    else:
        logger.info(f"Found existing Discord Forum Channel: #{forum_channel.name} (ID: {forum_channel.id})")

    # Update project channel binding in DB
    await task_service.project_service.update_project_channel(project.id, forum_channel.id)

    # 4. Generate Tech-Tree PNG Buffer & Pinned Hub Post
    logger.info("Rendering Civilization-style Tech Tree PNG graphic...")
    tree_buf = await task_service.render_project_tree(
        guild_id,
        project.id,
        orientation="lr",
        member_resolver=lambda uid: resolve_discord_member_name(uid, guild),
    )

    # Check if a pinned control center thread already exists
    active_threads: list[discord.Thread] = list(getattr(forum_channel, "threads", []))
    try:
        guild_threads = await guild.active_threads()
        for t in guild_threads:
            if t.parent_id == forum_channel.id and t not in active_threads:
                active_threads.append(t)
    except Exception as e:
        logger.debug(f"Failed to fetch guild active threads: {e}")

    if reset_existing:
        logger.info(f"Purging existing threads in forum channel #{forum_channel.name}...")
        for t in list(active_threads):
            try:
                await t.delete()
                logger.info(f"  Deleted old forum thread: {t.name}")
            except Exception as e:
                logger.debug(f"Could not delete old thread {t.name}: {e}")
        active_threads = []

    hub_thread = next(
        (t for t in active_threads if "Tech Tree & Project Control Center" in t.name or "📌" in t.name),
        None,
    )

    if not hub_thread:
        file = discord.File(fp=tree_buf, filename="tech_tree.png")
        embed = discord.Embed(
            title=f"🌲 Tech Tree: [{project.prefix}] {project.name}",
            description=(
                f"**Welcome to the [{project.prefix}] Technology Dependency Tree!**\n\n"
                "• **Civilization-Style DAG Visualizer**: Displays progressive dependency tiers and unlock paths.\n"
                "• **State Glowing Outlines**:\n"
                "  - 🟢 **Green / Complete**: Finished and unlocked downstream milestones.\n"
                "  - 🔵 **Cyan / In Progress**: Actively being researched and engineered.\n"
                "  - ⚪ **White / Ready**: All prerequisites completed — ready to start!\n"
                "  - 🔘 **Gray / Locked**: Blocked by uncompleted prerequisite tasks.\n"
                "  - 🔴 **Red / Blocked**: Manually flagged as blocked.\n\n"
                "Use the interactive buttons below to toggle layout orientations!"
            ),
            color=discord.Color.from_rgb(16, 152, 247),
        )
        embed.set_image(url="attachment://tech_tree.png")
        embed.set_footer(text="dgg-pm • Interactive Project Dependency System")

        viewer = TechTreeViewer(task_service, project, current_orientation="lr")
        try:
            hub_post_res = await forum_channel.create_thread(
                name=f"📌 [{project.prefix}] Tech Tree & Project Control Center",
                content="🌲 **Interactive Project Dependency Graph & Milestone Roadmap**",
                embed=embed,
                file=file,
                view=viewer,
                auto_archive_duration=10080,
            )
            hub_thread = getattr(hub_post_res, "thread", hub_post_res)
            try:
                await hub_thread.edit(pinned=True)
                logger.info(f"Created and pinned Tech Tree Hub post: '{hub_thread.name}'")
            except Exception as pe:
                logger.debug(f"Could not pin hub post (might require Manage Threads permission): {pe}")
        except Exception as e:
            logger.warning(f"Could not create pinned Tech Tree hub thread: {e}")

    # 5. Create Forum Thread Cards for each Task
    logger.info("Posting interactive task cards to Discord Forum...")
    for idx in sorted(tasks_by_index.keys()):
        task = tasks_by_index[idx]
        # Refresh task from DB to get latest dependencies
        fresh_task = await task_service.get_by_id(task.id)
        if not fresh_task:
            continue

        # If already posted to Discord, skip re-posting
        if fresh_task.discord_thread_id:
            logger.info(
                f"  Task [{fresh_task.short_id}] already posted to Discord (Thread ID: {fresh_task.discord_thread_id})"
            )
            continue

        prereqs, unlocks = await task_service.get_task_dependencies(fresh_task.id)
        embed = build_task_embed(
            fresh_task,
            project_name=project.name,
            prerequisites=prereqs,
            dependents=unlocks,
        )
        action_view = TaskActionView(
            task_id=fresh_task.id,
            current_status=fresh_task.status,
            current_priority=fresh_task.priority,
            task_service=task_service,
        )
        applied_tags = resolve_forum_tags(forum_channel, fresh_task)

        # Add Tech-Tree tag if available
        tree_tag = next((t for t in forum_channel.available_tags if "tech-tree" in t.name.lower()), None)
        if tree_tag and tree_tag not in applied_tags and len(applied_tags) < 5:
            applied_tags.append(tree_tag)

        # Add Blocked tag if task is blocked
        if fresh_task.metadata_json.get("blocked") is True:
            blocked_tag = next((t for t in forum_channel.available_tags if "blocked" in t.name.lower()), None)
            if blocked_tag and blocked_tag not in applied_tags and len(applied_tags) < 5:
                applied_tags.append(blocked_tag)

        # Clean up any existing thread for this task card if present
        existing_thread = next((t for t in active_threads if t.name.startswith(f"[{fresh_task.short_id}]")), None)
        if existing_thread:
            try:
                await existing_thread.delete()
                active_threads.remove(existing_thread)
            except Exception as e:
                logger.debug(f"Could not delete old thread {existing_thread.name}: {e}")

        try:
            thread_content = build_thread_workspace_content(fresh_task)
            res = await forum_channel.create_thread(
                name=f"[{fresh_task.short_id}] {fresh_task.title[:90]}",
                content=thread_content,
                embed=embed,
                view=action_view,
                applied_tags=applied_tags,
                auto_archive_duration=10080,
            )
            thread = getattr(res, "thread", res)
            message = getattr(res, "message", None)
            msg_id = message.id if message else thread.id

            await task_service.update_discord_message_ids(
                task_id=fresh_task.id,
                discord_message_id=msg_id,
                discord_thread_id=thread.id,
            )
            logger.info(f"  Created forum card: [{fresh_task.short_id}] -> Thread #{thread.name} ({thread.id})")
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"  Failed to create thread for [{fresh_task.short_id}]: {e}")

    await client.close()


def print_tech_tree_summary(
    project: Project, tasks_by_index: dict[int, Task], nodes_data: list[dict[str, Any]]
) -> None:
    """Prints a beautiful CLI summary of the seeded tech tree."""
    state_emojis = {
        "complete": "✅ COMPLETE   ",
        "active": "⏳ IN PROGRESS",
        "available": "✨ READY/OPEN ",
        "locked": "🔒 LOCKED     ",
        "blocked": "⛔ BLOCKED    ",
    }
    node_state_map = {n["key"]: n["state"] for n in nodes_data}

    print("\n" + "=" * 76)
    print(f"🌲 TECH TREE SEEDING COMPLETE: [{project.prefix}] {project.name}")
    print("=" * 76)
    print(f"Project ID : {project.id}")
    print(f"Total Tasks: {len(tasks_by_index)}")
    print("-" * 76)
    print(f"{'Task Key':<10} | {'State':<14} | {'Priority':<8} | {'Title'}")
    print("-" * 76)

    for idx in sorted(tasks_by_index.keys()):
        t = tasks_by_index[idx]
        state = node_state_map.get(t.short_id, "available")
        state_label = state_emojis.get(state, state)
        print(f"[{t.short_id:<8}] | {state_label} | {t.priority.value:<8} | {t.title}")

    print("=" * 76 + "\n")


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Seed a test project forum with a full DAG tech-tree, "
            "Civilization-style rendered PNG graph, and interactive Discord forum cards."
        )
    )
    parser.add_argument(
        "--guild-id",
        type=int,
        default=settings.DISCORD_GUILD_ID,
        help="Target Discord Guild ID (defaults to DISCORD_GUILD_ID in .env)",
    )
    parser.add_argument(
        "--name",
        type=str,
        default="Autonomous Swarm Platform",
        help="Project name (default: 'Autonomous Swarm Platform')",
    )
    parser.add_argument(
        "--prefix",
        type=str,
        default="TREE",
        help="Project prefix (default: 'TREE')",
    )
    parser.add_argument(
        "--channel-name",
        type=str,
        default="🌲-tech-tree-demo",
        help="Forum channel name (default: '🌲-tech-tree-demo')",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset existing project tasks and channel before seeding fresh.",
    )
    parser.add_argument(
        "--no-discord",
        action="store_true",
        help="Seed database only (skip Discord forum and channel interactions).",
    )

    args = parser.parse_args()

    check_production_safety_guard()

    guild_id = args.guild_id or (int(settings.DISCORD_GUILD_ID) if settings.DISCORD_GUILD_ID else 0)
    if not guild_id:
        logger.error("No guild ID provided. Specify --guild-id or set DISCORD_GUILD_ID in .env.")
        sys.exit(1)

    logger.info(f"🌱 Starting Tech Tree Seeding for Guild ID {guild_id}...")
    project, tasks_by_index, task_service = await seed_tech_tree_db(
        guild_id=guild_id,
        project_name=args.name,
        project_prefix=args.prefix,
        reset_existing=args.reset,
    )

    if not args.no_discord and settings.DISCORD_BOT_TOKEN:
        await seed_discord_forum(
            guild_id=guild_id,
            project=project,
            tasks_by_index=tasks_by_index,
            task_service=task_service,
            channel_name=args.channel_name,
            reset_existing=args.reset,
        )
    else:
        logger.info("Skipping Discord forum integration (--no-discord flag set or no bot token configured).")

    nodes_data, _ = await task_service.get_project_tree_data(guild_id, project.id)
    print_tech_tree_summary(project, tasks_by_index, nodes_data)
    await close_db()


if __name__ == "__main__":
    asyncio.run(main())
