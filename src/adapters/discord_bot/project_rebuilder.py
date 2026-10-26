"""Project workspace reconstruction and reconciliation helper."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

import discord

from src.adapters.discord_bot.views.forum_helpers import (
    ensure_pinned_hub_post,
    ensure_project_tag,
    setup_forum_tags,
)
from src.domain.exceptions import ProjectNotFoundError
from src.ports.workspace import (
    RebuildProgress,
    RebuildWorkspaceResult,
)

if TYPE_CHECKING:
    from src.adapters.discord_bot.project_workspace import DiscordProjectWorkspaceAdapter

logger = logging.getLogger("dgg_pm.project_rebuilder")


async def _maybe_await(res: Any) -> Any:
    """Awaits an object if it is an awaitable or coroutine."""
    if hasattr(res, "__await__"):
        return await res
    return res


async def rebuild_project_workspace(
    adapter: DiscordProjectWorkspaceAdapter,
    project_id: UUID,
    *,
    guild: discord.Guild,
    target_channel: discord.abc.GuildChannel | int | None = None,
    progress_callback: Any | None = None,
) -> RebuildWorkspaceResult:
    """Reconstructs and reconciles a Project's Discord presence from database state."""
    project = await adapter.project_service.get_by_id(project_id)
    if not project:
        raise ProjectNotFoundError(f"Project with ID '{project_id}' not found.")

    # If project is archived, unarchive it
    if project.is_archived:
        unarchived = await adapter.project_service.unarchive_project(project.id)
        if unarchived:
            project = unarchived

    forum_created = False
    resolved_channel = None
    warnings: list[str] = []

    if progress_callback:
        await _maybe_await(
            progress_callback(
                RebuildProgress(
                    step="channel",
                    current=1,
                    total=4,
                    message=f"Resolving channel for project [{project.prefix}] {project.name}...",
                )
            )
        )

    # 1. Target channel resolution
    if target_channel is not None:
        raw_channel = await adapter._resolve_channel(target_channel)
        validated = adapter._validate_channel_type(raw_channel)
        if validated:
            resolved_channel = validated
            await adapter.project_service.update_project_channel(project.id, validated.id)
            project = await adapter.project_service.get_by_id(project.id) or project
    else:
        if project.discord_channel_id:
            chan = None
            if hasattr(guild, "get_channel"):
                chan = guild.get_channel(project.discord_channel_id)
            if not chan and hasattr(guild, "fetch_channel"):
                try:
                    chan = await _maybe_await(guild.fetch_channel(project.discord_channel_id))
                except Exception:
                    chan = None
            if chan and isinstance(chan, (discord.ForumChannel, discord.TextChannel)):
                resolved_channel = chan

        if not resolved_channel:
            # Auto-create ForumChannel
            target_category = None
            guild_categories = getattr(guild, "categories", [])
            if project.category:
                target_category = next(
                    (c for c in guild_categories if c.name.lower() == project.category.lower()), None
                )
            if not target_category:
                target_category = next(
                    (c for c in guild_categories if "dgg-pm" in c.name.lower() or "projects" in c.name.lower()),
                    None,
                )

            overwrites = {}
            role_ids = project.discord_role_ids or ([project.discord_role_id] if project.discord_role_id else [])
            for rid in role_ids:
                role_obj = guild.get_role(rid) if hasattr(guild, "get_role") else None
                if role_obj:
                    overwrites[role_obj] = discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=True,
                        send_messages_in_threads=True,
                        create_public_threads=True,
                        attach_files=True,
                        embed_links=True,
                    )
            if project.lead_discord_id:
                lead_member = guild.get_member(project.lead_discord_id) if hasattr(guild, "get_member") else None
                if lead_member:
                    overwrites[lead_member] = discord.PermissionOverwrite(
                        view_channel=True,
                        send_messages=True,
                        send_messages_in_threads=True,
                        create_public_threads=True,
                        manage_threads=True,
                    )

            clean_name = f"📁-{project.name.lower().replace(' ', '-')}"
            try:
                create_kwargs: dict[str, Any] = {
                    "name": clean_name,
                    "category": target_category,
                    "reason": f"dgg-pm workspace rebuild for project {project.name}",
                }
                if overwrites:
                    create_kwargs["overwrites"] = overwrites
                create_fn = getattr(guild, "create_forum", None) or getattr(guild, "create_forum_channel", None)
                if not create_fn:
                    raise AttributeError("Target Discord guild does not support creating forum channels.")
                new_forum = await _maybe_await(create_fn(**create_kwargs))
                resolved_channel = new_forum
                forum_created = True
                await adapter.project_service.update_project_channel(project.id, new_forum.id)
                project = await adapter.project_service.get_by_id(project.id) or project
            except Exception as e:
                logger.error("Failed to auto-create ForumChannel for project %s: %s", project.id, e)
                warnings.append(f"Could not auto-create Forum Channel: {e}")

    # 2. Forum tags & Control Hub
    tags_created = 0
    hub_rebuilt = False
    if resolved_channel and isinstance(resolved_channel, discord.ForumChannel):
        if progress_callback:
            await _maybe_await(
                progress_callback(
                    RebuildProgress(
                        step="tags",
                        current=2,
                        total=4,
                        message=f"Configuring standard PM tags in #{resolved_channel.name}...",
                    )
                )
            )
        added, _total, tag_err = await setup_forum_tags(resolved_channel)
        tags_created += added
        if tag_err:
            warnings.append(tag_err)
        proj_tag_err = await ensure_project_tag(resolved_channel, project.name)
        if proj_tag_err:
            warnings.append(proj_tag_err)

    if resolved_channel:
        if progress_callback:
            await _maybe_await(
                progress_callback(
                    RebuildProgress(
                        step="hub",
                        current=3,
                        total=4,
                        message="Mounting pinned Control Hub post...",
                    )
                )
            )
        try:
            hub_ok, hub_status = await ensure_pinned_hub_post(
                channel=resolved_channel,
                project_service=adapter.project_service,
                squad_service=adapter.squad_service,
                task_service=adapter.task_service,
                user_service=adapter.user_service,
                project_name=project.name,
            )
            hub_rebuilt = hub_ok
            if not hub_ok:
                warnings.append(hub_status)
        except Exception as e:
            warnings.append(f"Could not mount Control Hub: {e}")

    # 3. Task Thread Reconciliation
    tasks_reconciled = 0
    tasks_recreated = 0
    tasks_archived = 0

    task_ws = adapter._effective_task_workspace
    if resolved_channel and adapter.task_service and task_ws:
        tasks, _ = await adapter.task_service.list_tasks(
            guild_id=guild.id,
            project_id=project.id,
            limit=1000,
            include_archived=True,
        )
        tasks.sort(key=lambda t: t.task_number)
        total_tasks = len(tasks)

        for idx, task in enumerate(tasks, start=1):
            if progress_callback:
                await _maybe_await(
                    progress_callback(
                        RebuildProgress(
                            step="tasks",
                            current=idx,
                            total=total_tasks,
                            message=f"Reconciling task [{task.short_id}] ({idx}/{total_tasks})...",
                        )
                    )
                )

            # Check if thread exists in Discord
            thread = None
            if task.discord_thread_id:
                if hasattr(guild, "get_channel"):
                    thread = guild.get_channel(task.discord_thread_id)
                if not thread and hasattr(adapter.bot, "get_channel"):
                    thread = adapter.bot.get_channel(task.discord_thread_id)
                if not thread and hasattr(guild, "fetch_channel"):
                    try:
                        thread = await _maybe_await(guild.fetch_channel(task.discord_thread_id))
                    except Exception:
                        thread = None

            if thread and isinstance(thread, discord.Thread):
                try:
                    sync_res = await task_ws.sync_workspace(
                        task,
                        project=project,
                        project_name=project.name,
                        sync_title=True,
                        sync_tags=True,
                        sync_archive=True,
                        sync_starter_card=True,
                    )
                    tasks_reconciled += 1
                    if hasattr(sync_res, "title_deferred") and sync_res.title_deferred:
                        warnings.append(
                            f"Task {task.short_id} title rename deferred due to Discord rate limits (cooldown active)."
                        )
                except Exception as e:
                    logger.warning("Failed to sync workspace for task %s: %s", task.short_id, e)
                    warnings.append(f"Failed to sync task {task.short_id}: {e}")
            else:
                try:
                    task, ref = await adapter.task_service.provision_task_workspace(
                        task.id,
                        target_container=resolved_channel,
                        workspace=task_ws,
                    )
                    tasks_recreated += 1

                    # Archive Invariant: If task is completed or archived, immediately archive the thread
                    if task.is_completed or task.is_archived:
                        created_thread = None
                        if hasattr(adapter.bot, "get_channel"):
                            created_thread = adapter.bot.get_channel(ref.thread_id)
                        if not created_thread and hasattr(guild, "get_channel"):
                            created_thread = guild.get_channel(ref.thread_id)
                        if not created_thread and hasattr(guild, "fetch_channel"):
                            try:
                                created_thread = await _maybe_await(guild.fetch_channel(ref.thread_id))
                            except Exception:
                                created_thread = None

                        if created_thread and hasattr(created_thread, "edit"):
                            await _maybe_await(created_thread.edit(archived=True))
                            tasks_archived += 1
                except Exception as e:
                    logger.error("Failed to provision thread workspace for task %s: %s", task.short_id, e)
                    warnings.append(f"Failed to rebuild task {task.short_id}: {e}")

            if idx < total_tasks:
                await asyncio.sleep(0.4)

    channel_id = resolved_channel.id if resolved_channel else (project.discord_channel_id or 0)
    return RebuildWorkspaceResult(
        project=project,
        channel_id=channel_id,
        forum_created=forum_created,
        tags_created=tags_created,
        hub_rebuilt=hub_rebuilt,
        tasks_reconciled=tasks_reconciled,
        tasks_recreated=tasks_recreated,
        tasks_archived=tasks_archived,
        warnings=tuple(warnings),
    )
