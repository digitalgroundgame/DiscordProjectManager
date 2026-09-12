from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import discord

from src.adapters.discord_bot.views.forum_helpers import (
    PROJECT_TAG_EMOJI,
    STANDARD_PM_TAG_DEFINITIONS,
    ensure_pinned_hub_post,
    project_tag_name,
    resolve_forum_tags,
)
from src.adapters.discord_bot.views.task_buttons import TaskActionView
from src.adapters.discord_bot.views.task_embed import (
    build_task_embed,
    build_thread_workspace_content,
)
from src.services.seed.manifest_loader import SeedManifest, SquadSeedSpec

if TYPE_CHECKING:
    from src.services.seed.seed_service import SeedDbResult
    from src.services.task_service import TaskService
    from src.services.team_service import TeamService
    from src.services.user_service import UserService

logger = logging.getLogger("seed.discord_provisioner")

DEFAULT_MANAGED_CATEGORY = "📁 DGG-PM Projects"


async def resolve_and_provision_guild_roles(guild: discord.Guild, squads: list[SquadSeedSpec]) -> dict[str, int]:
    """Finds existing roles in guild or creates missing roles, returning role_name -> role_id mapping."""
    if hasattr(guild, "fetch_roles"):
        try:
            roles = await guild.fetch_roles()
        except Exception:
            roles = guild.roles
    else:
        roles = guild.roles

    existing_roles = {r.name.lower(): r for r in roles}
    mapping: dict[str, int] = {}

    for squad in squads:
        target_name = squad.role_name or squad.name
        if target_name.lower() in existing_roles:
            role = existing_roles[target_name.lower()]
            mapping[target_name] = role.id
            logger.info("Using existing Discord role '%s' (ID: %s)", target_name, role.id)
        else:
            try:
                new_role = await guild.create_role(name=target_name)
                mapping[target_name] = new_role.id
                existing_roles[target_name.lower()] = new_role
                logger.info("Created Discord role '%s' (ID: %s)", target_name, new_role.id)
            except discord.Forbidden:
                logger.error(
                    "⛔ Missing Permissions: Bot cannot create Discord role '%s'. "
                    "Ensure the bot has the 'Manage Roles' permission enabled in Discord Server Settings.",
                    target_name,
                )
            except Exception as e:
                logger.warning("Could not create Discord role '%s': %s", target_name, e)

    return mapping


async def clean_managed_category_channels(guild: discord.Guild, category_name: str = DEFAULT_MANAGED_CATEGORY) -> int:
    """Strictly deletes channels within the managed category and the category itself."""
    channels = await guild.fetch_channels()
    category = next(
        (c for c in channels if isinstance(c, discord.CategoryChannel) and c.name.lower() == category_name.lower()),
        None,
    )

    if not category:
        logger.info("Managed category '%s' not found; nothing to clean.", category_name)
        return 0

    deleted_count = 0
    for channel in channels:
        if getattr(channel, "category_id", None) == category.id:
            try:
                logger.info("Deleting seeded channel: #%s (%s)", channel.name, channel.id)
                await channel.delete(reason="dgg-pm declarative seed reset")
                deleted_count += 1
                await asyncio.sleep(0.4)
            except Exception as e:
                logger.error("Failed to delete channel #%s: %s", channel.name, e)

    try:
        logger.info("Deleting managed category: %s (%s)", category.name, category.id)
        await category.delete(reason="dgg-pm declarative seed reset")
    except Exception as e:
        logger.error("Failed to delete category %s: %s", category.name, e)

    return deleted_count


async def provision_discord_workspaces(
    guild: discord.Guild,
    manifest: SeedManifest,
    db_result: SeedDbResult,
    task_service: TaskService,
    team_service: TeamService | None = None,
    user_service: UserService | None = None,
    category_name: str = DEFAULT_MANAGED_CATEGORY,
) -> None:
    """Provisions Discord Category, Forum Channels, Pinned Hubs, and Task Action Cards."""
    channels = await guild.fetch_channels()

    # 1. Find or create Category
    category = next(
        (c for c in channels if isinstance(c, discord.CategoryChannel) and c.name.lower() == category_name.lower()),
        None,
    )
    if not category:
        try:
            category = await guild.create_category(category_name)
            channels.append(category)
            logger.info("Created Category: %s (%s)", category.name, category.id)
        except Exception as e:
            logger.warning("Could not create category '%s': %s", category_name, e)

    # 2. Provision Channels and Bind to Projects
    channel_objs: dict[str, discord.abc.GuildChannel] = {}

    for chan_spec in manifest.channels:
        existing_chan = next(
            (c for c in channels if c.name.lower() == chan_spec.name.lower()),
            None,
        )

        if not existing_chan:
            if chan_spec.type == "forum":
                tags = [discord.ForumTag(name=d["name"], emoji=d["emoji"]) for d in STANDARD_PM_TAG_DEFINITIONS]
                for prefix in chan_spec.projects:
                    proj = db_result.projects_by_prefix.get(prefix.upper())
                    if proj:
                        tags.append(discord.ForumTag(name=project_tag_name(proj.name), emoji=PROJECT_TAG_EMOJI))
                existing_chan = await guild.create_forum(
                    name=chan_spec.name,
                    category=category,
                    topic=chan_spec.topic or "",
                    available_tags=tags,
                )
            else:
                existing_chan = await guild.create_text_channel(
                    name=chan_spec.name,
                    category=category,
                    topic=chan_spec.topic or "",
                )
            channels.append(existing_chan)
            await asyncio.sleep(0.4)

        channel_objs[chan_spec.name] = existing_chan

        # Bind channel ID in DB for each associated project
        for prefix in chan_spec.projects:
            proj = db_result.projects_by_prefix.get(prefix.upper())
            if proj:
                await task_service.project_service.update_project_channel(proj.id, existing_chan.id)
                proj.discord_channel_id = existing_chan.id

        # Pinned Control Hub post for forums
        if isinstance(existing_chan, discord.ForumChannel):
            await ensure_pinned_hub_post(
                channel=existing_chan,
                project_service=task_service.project_service,
                team_service=team_service,
                task_service=task_service,
                user_service=user_service,
            )
            await asyncio.sleep(0.5)

    # 3. Post Task Thread Workspaces & Action Cards
    # Map project prefix to channel
    proj_prefix_to_chan: dict[str, discord.abc.GuildChannel] = {}
    for chan_spec in manifest.channels:
        chan_obj = channel_objs.get(chan_spec.name)
        if chan_obj:
            for prefix in chan_spec.projects:
                proj_prefix_to_chan[prefix.upper()] = chan_obj

    # Cache existing forum threads to support idempotent non-destructive re-seeding
    existing_forum_threads: dict[int, list[discord.Thread]] = {}
    for chan in channel_objs.values():
        if isinstance(chan, discord.ForumChannel):
            th_list = list(getattr(chan, "threads", []) or [])
            if hasattr(chan, "active_threads"):
                try:
                    act_res = chan.active_threads()
                    act = await act_res if hasattr(act_res, "__await__") else act_res
                    th_list.extend(getattr(act, "threads", act) or [])
                except Exception:
                    pass
            existing_forum_threads[chan.id] = th_list

    total_tasks = len(manifest.tasks)
    logger.info("Posting %s interactive task cards to Discord workspaces (rate-limit paced)...", total_tasks)

    for i, task_spec in enumerate(manifest.tasks, 1):
        proj = db_result.projects_by_prefix.get(task_spec.project.upper())
        if not proj:
            continue
        chan = proj_prefix_to_chan.get(proj.prefix)
        if not chan:
            continue

        task = db_result.tasks_by_slug.get(task_spec.slug) if task_spec.slug else None
        if not task:
            continue

        prereqs, unlocks = await task_service.get_task_dependencies(task.id)
        embed = build_task_embed(
            task,
            project_name=proj.name,
            prerequisites=prereqs,
            dependents=unlocks,
        )
        action_view = TaskActionView(
            task_id=task.id,
            current_status=task.status,
            current_priority=task.priority,
            task_service=task_service,
        )
        thread_content = build_thread_workspace_content(task)

        if isinstance(chan, discord.ForumChannel):
            # Check for existing thread for this task card
            existing_th = next(
                (t for t in existing_forum_threads.get(chan.id, []) if t.name.startswith(f"[{task.short_id}]")),
                None,
            )
            if existing_th:
                logger.info(
                    "  [%s/%s] Reusing existing thread: [%s] #%s",
                    i,
                    total_tasks,
                    task.short_id,
                    existing_th.name,
                )
                await task_service.update_discord_message_ids(
                    task_id=task.id,
                    discord_message_id=existing_th.id,
                    discord_thread_id=existing_th.id,
                )
                continue

            applied_tags = resolve_forum_tags(chan, task, project_name=proj.name)
            res = await chan.create_thread(
                name=f"[{task.short_id}] {task.title[:90]}",
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
                task_id=task.id,
                discord_message_id=msg_id,
                discord_thread_id=thread.id,
            )
            logger.info("  [%s/%s] Created forum card: [%s] %s", i, total_tasks, task.short_id, task.title[:35])
            await asyncio.sleep(1.2)
        elif isinstance(chan, discord.TextChannel):
            msg = await chan.send(embed=embed)
            thread = await msg.create_thread(
                name=f"[{task.short_id}] {task.title[:90]}",
                auto_archive_duration=10080,
            )
            await thread.send(content=thread_content, view=action_view)
            await task_service.update_discord_message_ids(
                task_id=task.id,
                discord_message_id=msg.id,
                discord_thread_id=thread.id,
            )
            logger.info("  [%s/%s] Created thread card: [%s] %s", i, total_tasks, task.short_id, task.title[:35])
            await asyncio.sleep(1.2)
