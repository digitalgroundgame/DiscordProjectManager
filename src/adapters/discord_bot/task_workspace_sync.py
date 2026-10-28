"""Task Discord Thread Workspace synchronization helper."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import discord

from src.adapters.discord_bot.views.forum_helpers import (
    resolve_forum_tags,
    unarchive_thread_if_needed,
)
from src.adapters.discord_bot.views.task_buttons import TaskActionView
from src.adapters.discord_bot.views.task_embed import (
    build_task_embed,
    build_thread_workspace_content,
)
from src.domain.enums import TaskStatus
from src.domain.models import Project, Task
from src.ports.workspace import SyncWorkspaceResult

if TYPE_CHECKING:
    from src.adapters.discord_bot.task_workspace import DiscordTaskWorkspaceAdapter

logger = logging.getLogger("dgg_pm.task_workspace_sync")


async def _maybe_await(res: Any) -> Any:
    """Awaits an object if it is an awaitable or coroutine."""
    if hasattr(res, "__await__"):
        return await res
    return res


def _is_thread_archived_error(exc: Exception) -> bool:
    """Returns True if the exception is Discord HTTP 400 error code 50083 (Thread is archived)."""
    if getattr(exc, "code", None) == 50083:
        return True
    msg = str(exc)
    return "50083" in msg or "Thread is archived" in msg


async def sync_task_workspace_state(
    adapter: DiscordTaskWorkspaceAdapter,
    task: Task,
    *,
    project: Project | None = None,
    project_name: str | None = None,
    sync_title: bool = False,
    sync_tags: bool = True,
    sync_archive: bool = True,
    sync_starter_card: bool = True,
) -> SyncWorkspaceResult:
    """Synchronizes an existing Thread Workspace with the current Task domain model state."""
    if not task.discord_thread_id:
        return SyncWorkspaceResult(success=False)

    try:
        thread = None
        if hasattr(adapter.bot, "get_channel"):
            thread = adapter.bot.get_channel(task.discord_thread_id)
        if not thread and hasattr(adapter.bot, "fetch_channel"):
            thread = await _maybe_await(adapter.bot.fetch_channel(task.discord_thread_id))
    except Exception as e:
        logger.debug("Could not fetch thread %s for task %s: %s", task.discord_thread_id, task.short_id, e)
        return SyncWorkspaceResult(success=False)

    if not isinstance(thread, discord.Thread):
        return SyncWorkspaceResult(success=False)

    resolved_proj_name = project_name or (project.name if project else None)
    if not resolved_proj_name and task.project_id and adapter.project_service:
        try:
            p = await adapter.project_service.get_by_id(task.project_id)
            if p:
                resolved_proj_name = p.name
        except Exception:
            pass

    was_archived = bool(getattr(thread, "archived", False))
    is_done = task.status == TaskStatus.COMPLETED or task.is_archived
    target_archived = is_done if sync_archive else was_archived

    # 1. Sync Starter Message Embed / Card
    if sync_starter_card and task.discord_message_id:
        try:
            root_msg = None
            if isinstance(thread.parent, discord.ForumChannel):
                root_msg = thread.starter_message
                if not root_msg and hasattr(thread, "fetch_message"):
                    root_msg = await _maybe_await(thread.fetch_message(task.discord_message_id))
            elif thread.parent and hasattr(thread.parent, "fetch_message"):
                root_msg = await _maybe_await(thread.parent.fetch_message(task.discord_message_id))

            if root_msg and hasattr(root_msg, "edit"):
                prereqs, dependents = await adapter._get_dependencies_safe(task.id)
                fresh_embed = build_task_embed(
                    task,
                    project_name=resolved_proj_name,
                    prerequisites=prereqs,
                    dependents=dependents,
                )
                fresh_view = TaskActionView(
                    task_id=task.id,
                    current_status=task.status,
                    current_priority=task.priority,
                    task_service=adapter.task_service,
                    current_assignee_id=task.assignee_discord_id,
                    current_watchers=task.watchers,
                )
                # Unarchive if needed, but defer re-archiving until Step 2 finishes
                async with unarchive_thread_if_needed(thread, keep_archived=False):
                    try:
                        if isinstance(thread.parent, discord.ForumChannel):
                            thread_content = build_thread_workspace_content(task)
                            await _maybe_await(
                                root_msg.edit(content=thread_content, embed=fresh_embed, view=fresh_view)
                            )
                        else:
                            await _maybe_await(root_msg.edit(embed=fresh_embed, view=fresh_view))
                    except Exception as edit_err:
                        if _is_thread_archived_error(edit_err) and hasattr(thread, "edit"):
                            await _maybe_await(thread.edit(archived=False))
                            thread.archived = False
                            if isinstance(thread.parent, discord.ForumChannel):
                                thread_content = build_thread_workspace_content(task)
                                await _maybe_await(
                                    root_msg.edit(content=thread_content, embed=fresh_embed, view=fresh_view)
                                )
                            else:
                                await _maybe_await(root_msg.edit(embed=fresh_embed, view=fresh_view))
                        else:
                            raise
        except Exception as e:
            logger.debug("Failed to sync starter embed for task %s: %s", task.short_id, e)

    # 2. Sync Thread Attributes (tags, title, archive state)
    edit_kwargs: dict[str, object] = {}
    title_renamed = False
    title_deferred = False
    cooldown_remaining = 0.0

    if sync_tags and isinstance(thread.parent, discord.ForumChannel):
        raw_existing = getattr(thread, "applied_tags", None)
        existing_tags = list(raw_existing) if isinstance(raw_existing, (list, tuple)) else None
        tags_to_apply = resolve_forum_tags(
            thread.parent,
            task=task,
            project_name=resolved_proj_name,
            existing_tags=existing_tags,
        )
        existing_ids = [getattr(t, "id", t) for t in existing_tags] if existing_tags is not None else None
        target_ids = [getattr(t, "id", t) for t in tags_to_apply]
        if existing_ids != target_ids:
            edit_kwargs["applied_tags"] = tags_to_apply

    expected_name = None
    if sync_title:
        expected_name = f"[{task.short_id}] {task.title}"
        if len(expected_name) > 100:
            expected_name = expected_name[:97] + "..."
        if getattr(thread, "name", None) != expected_name:
            can_rename, _ = adapter.rename_limiter.can_rename(thread.id)
            if can_rename:
                edit_kwargs["name"] = expected_name
            else:
                rename_res = await adapter.rename_limiter.request_rename(thread, expected_name)
                title_deferred = rename_res.deferred
                cooldown_remaining = rename_res.cooldown_remaining_seconds

    currently_archived = bool(getattr(thread, "archived", False))
    has_non_archive_edits = bool(edit_kwargs)

    if target_archived and currently_archived and has_non_archive_edits and hasattr(thread, "edit"):
        try:
            await _maybe_await(thread.edit(archived=False))
            thread.archived = False
            currently_archived = False
        except Exception as unarch_err:
            logger.debug("Failed to unarchive thread %s before state edit: %s", thread.id, unarch_err)

    if target_archived and not currently_archived:
        edit_kwargs["archived"] = True
    elif not target_archived and currently_archived:
        edit_kwargs["archived"] = False

    if edit_kwargs and hasattr(thread, "edit"):
        logger.debug(
            "Syncing thread workspace attributes for task [%s] (thread %s): %s",
            task.short_id,
            thread.id,
            list(edit_kwargs.keys()),
        )
        try:
            await _maybe_await(thread.edit(**edit_kwargs))
            if "archived" in edit_kwargs:
                thread.archived = bool(edit_kwargs["archived"])
            if "applied_tags" in edit_kwargs:
                try:
                    thread.applied_tags = edit_kwargs["applied_tags"]
                except Exception:
                    pass
            if "name" in edit_kwargs:
                adapter.rename_limiter.record_rename(thread.id)
                title_renamed = True
        except Exception as e:
            if _is_thread_archived_error(e):
                if list(edit_kwargs.keys()) == ["archived"] and edit_kwargs.get("archived") is True:
                    thread.archived = True
                else:
                    try:
                        await _maybe_await(thread.edit(archived=False))
                        thread.archived = False
                        if target_archived:
                            edit_kwargs["archived"] = True
                        await _maybe_await(thread.edit(**edit_kwargs))
                        if "archived" in edit_kwargs:
                            thread.archived = bool(edit_kwargs["archived"])
                        if "applied_tags" in edit_kwargs:
                            try:
                                thread.applied_tags = edit_kwargs["applied_tags"]
                            except Exception:
                                pass
                        if "name" in edit_kwargs:
                            adapter.rename_limiter.record_rename(thread.id)
                            title_renamed = True
                    except Exception as retry_err:
                        logger.warning(
                            "Failed to edit thread state after unarchiving for task %s (%s): %s",
                            task.short_id,
                            task.discord_thread_id,
                            retry_err,
                        )
            else:
                status = getattr(e, "status", None)
                retry_after = getattr(e, "retry_after", None)
                if "name" in edit_kwargs and (status == 429 or retry_after is not None):
                    backoff = float(retry_after) if retry_after else 600.0
                    logger.warning(
                        "Discord HTTP 429 encountered during thread rename for task [%s] (%s). Backing off for %.1fs",
                        task.short_id,
                        thread.id,
                        backoff,
                    )
                    adapter.rename_limiter.record_rate_limit(thread.id, backoff)
                    rename_res = await adapter.rename_limiter.request_rename(thread, edit_kwargs["name"])
                    title_deferred = rename_res.deferred
                    cooldown_remaining = rename_res.cooldown_remaining_seconds

                    remaining_kwargs = {k: v for k, v in edit_kwargs.items() if k != "name"}
                    if remaining_kwargs:
                        try:
                            await _maybe_await(thread.edit(**remaining_kwargs))
                            if "archived" in remaining_kwargs:
                                thread.archived = bool(remaining_kwargs["archived"])
                            if "applied_tags" in remaining_kwargs:
                                try:
                                    thread.applied_tags = remaining_kwargs["applied_tags"]
                                except Exception:
                                    pass
                        except Exception as rem_err:
                            logger.warning(
                                "Failed to edit thread attributes without name for task %s: %s",
                                task.short_id,
                                rem_err,
                            )
                elif status == 429 or retry_after is not None:
                    logger.warning(
                        "Discord HTTP 429 rate limit on editing thread attributes %s for task [%s] (%s). "
                        "Retry after: %ss",
                        list(edit_kwargs.keys()),
                        task.short_id,
                        task.discord_thread_id,
                        retry_after,
                    )
                else:
                    logger.warning(
                        "Failed to edit thread state for task %s (%s): %s",
                        task.short_id,
                        task.discord_thread_id,
                        e,
                    )

    return SyncWorkspaceResult(
        success=True,
        title_renamed=title_renamed,
        title_deferred=title_deferred,
        cooldown_remaining_seconds=cooldown_remaining,
    )
