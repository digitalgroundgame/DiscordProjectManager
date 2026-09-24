from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import discord

from src.domain.enums import PriorityLevel, TaskStatus

if TYPE_CHECKING:
    from src.domain.models import Task
    from src.services.auth_service import AuthService
    from src.services.project_service import ProjectService
    from src.services.squad_service import SquadService
    from src.services.task_service import TaskService
    from src.services.user_service import UserService

logger = logging.getLogger("dgg_pm.views.forum_helpers")


@asynccontextmanager
async def unarchive_thread_if_needed(
    thread: discord.Thread | None,
    keep_archived: bool = True,
):
    """Context manager that temporarily unarchives an archived Discord thread to allow safe edits/interaction responses.

    If `keep_archived` is True and the thread was originally archived, restores the archived state on exit.
    """
    if not thread or not isinstance(thread, discord.Thread) or not getattr(thread, "archived", False):
        yield
        return

    was_archived = thread.archived
    try:
        if hasattr(thread, "edit"):
            edit_res = thread.edit(archived=False)
            if hasattr(edit_res, "__await__"):
                await edit_res
            thread.archived = False
    except Exception as e:
        logger.debug("Failed to unarchive thread %s: %s", getattr(thread, "id", None), e)

    try:
        yield
    finally:
        if was_archived and keep_archived:
            try:
                if hasattr(thread, "edit"):
                    edit_res = thread.edit(archived=True)
                    if hasattr(edit_res, "__await__"):
                        await edit_res
                    thread.archived = True
            except Exception as e:
                logger.debug("Failed to restore archived state for thread %s: %s", getattr(thread, "id", None), e)


STATUS_KEYWORDS = {
    TaskStatus.NOT_STARTED: ["not started", "to do", "todo", "backlog", "open"],
    TaskStatus.IN_PROGRESS: ["in progress", "doing", "active", "wip", "in-progress"],
    TaskStatus.COMPLETED: ["completed", "done", "closed", "resolved", "complete"],
}

PRIORITY_KEYWORDS = {
    PriorityLevel.HIGH: ["high", "urgent", "critical", "p0", "p1", "high priority", "p:high"],
    PriorityLevel.NORMAL: ["normal", "medium", "p2", "normal priority", "p:normal"],
    PriorityLevel.LOW: ["low", "minor", "p3", "low priority", "p:low"],
}

UNASSIGNED_KEYWORDS = ["unassigned", "not assigned", "un-assigned", "no assignee", "needs assignee"]
OVERDUE_KEYWORDS = ["overdue", "late", "past due"]

STANDARD_PM_TAG_DEFINITIONS = [
    {"name": "Not Started", "emoji": "⏳", "keywords": ["not started", "to do", "todo", "backlog", "open"]},
    {"name": "In Progress", "emoji": "🟡", "keywords": ["in progress", "doing", "active", "wip", "in-progress"]},
    {"name": "Completed", "emoji": "✅", "keywords": ["completed", "done", "closed", "resolved", "complete"]},
    {
        "name": "High Priority",
        "emoji": "🔴",
        "keywords": ["high", "urgent", "critical", "p0", "p1", "high priority", "p:high"],
    },
    {"name": "Normal Priority", "emoji": "🟡", "keywords": ["normal", "medium", "p2", "normal priority", "p:normal"]},
    {"name": "Low Priority", "emoji": "🟢", "keywords": ["low", "minor", "p3", "low priority", "p:low"]},
    {
        "name": "Unassigned",
        "emoji": "👤",
        "keywords": UNASSIGNED_KEYWORDS,
    },
    {
        "name": "Overdue",
        "emoji": "⏰",
        "keywords": OVERDUE_KEYWORDS,
    },
]

PROJECT_TAG_EMOJI = "📁"


def project_tag_name(project_name: str) -> str:
    """Builds the per-project forum tag name, e.g. 'Mobile App'."""
    return project_name.strip()


def _matches_project_tag(tag_name: str, project_name: str | None) -> bool:
    """Checks if a tag name is the per-project tag for the given project."""
    if not project_name:
        return False
    expected = project_name.strip().lower()
    clean = tag_name.lower().replace(PROJECT_TAG_EMOJI, "").replace("[", "").replace("]", "").strip()
    return clean == expected


def _matches_keyword(tag_name: str, keywords: list[str]) -> bool:
    """Checks if a tag name matches any of the target keywords (ignoring emojis/brackets/case)."""
    clean_name = tag_name.lower().replace("[", "").replace("]", "").replace(":", " ").strip()
    words = clean_name.split()
    for kw in keywords:
        if kw == clean_name or kw in words:
            return True
    return False


def resolve_forum_tags(
    forum_channel: discord.ForumChannel,
    task: Task | None = None,
    *,
    status: TaskStatus | None = None,
    priority: PriorityLevel | None = None,
    is_unassigned: bool | None = None,
    is_overdue: bool | None = None,
    project_name: str | None = None,
    existing_tags: list[discord.ForumTag] | None = None,
) -> list[discord.ForumTag]:
    """Finds matching ForumTag objects for a task's status, priority, overdue, and unassigned state.

    Applies the per-project tag when a ``project_name`` is provided. Preserves any
    non-status/priority/overdue/unassigned/project custom tags already applied on the thread
    (up to Discord's maximum limit of 5 applied tags per thread, prioritizing status,
    priority, overdue, and project/squad tags over unassigned state).
    """
    if not hasattr(forum_channel, "available_tags") or not forum_channel.available_tags:
        return []

    if task is not None:
        status = status or task.status
        priority = priority or task.priority
        if is_unassigned is None:
            is_unassigned = task.assignee_discord_id is None
        if is_overdue is None:
            is_overdue = task.is_overdue

    target_status = status or TaskStatus.NOT_STARTED
    target_priority = priority or PriorityLevel.NORMAL
    unassigned_flag = bool(is_unassigned)
    overdue_flag = bool(is_overdue)

    available = forum_channel.available_tags
    applied: list[discord.ForumTag] = []

    # 1. Match status tag
    status_kws = STATUS_KEYWORDS.get(target_status, [])
    for tag in available:
        if _matches_keyword(tag.name, status_kws):
            applied.append(tag)
            break

    # 2. Match priority tag
    prio_kws = PRIORITY_KEYWORDS.get(target_priority, [])
    for tag in available:
        if _matches_keyword(tag.name, prio_kws):
            if tag not in applied:
                applied.append(tag)
            break

    # 3. Match Overdue tag if task is overdue
    if overdue_flag:
        for tag in available:
            if _matches_keyword(tag.name, OVERDUE_KEYWORDS):
                if tag not in applied:
                    applied.append(tag)
                break

    # 4. Match per-project tag
    if project_name:
        for tag in available:
            if _matches_project_tag(tag.name, project_name):
                if tag not in applied:
                    applied.append(tag)
                break

    # 5. Preserve custom tags (e.g. squad or category tags) that don't represent
    # status, priority, overdue, unassigned, or project state
    if existing_tags:
        all_managed_kws = [
            kw
            for kws in list(STATUS_KEYWORDS.values())
            + list(PRIORITY_KEYWORDS.values())
            + [UNASSIGNED_KEYWORDS, OVERDUE_KEYWORDS]
            for kw in kws
        ]
        for tag in existing_tags:
            if not _matches_keyword(tag.name, all_managed_kws) and not _matches_project_tag(tag.name, project_name):
                if tag not in applied:
                    applied.append(tag)

    # 6. Match Unassigned tag if unassigned (lowest priority under the 5-tag cap)
    if unassigned_flag:
        for tag in available:
            if _matches_keyword(tag.name, UNASSIGNED_KEYWORDS):
                if tag not in applied:
                    applied.append(tag)
                break

    return applied[:5]


async def sync_thread_forum_tags(
    thread: discord.Thread,
    task: Task,
    *,
    project_name: str | None = None,
) -> list[discord.ForumTag]:
    """Synchronizes forum tags on a thread workspace to reflect the current task state.

    Resolves tags via resolve_forum_tags (applying ⏰ Overdue when task is overdue,
    alongside status, priority, project, and squad tags up to the 5-tag cap) and
    updates the thread if tags have changed.
    """
    parent = getattr(thread, "parent", None)
    if not isinstance(parent, discord.ForumChannel):
        return []

    existing = getattr(thread, "applied_tags", None) or []
    tags = resolve_forum_tags(
        parent,
        task=task,
        project_name=project_name,
        existing_tags=existing,
    )

    if hasattr(thread, "edit"):
        edit_res = thread.edit(applied_tags=tags)
        if hasattr(edit_res, "__await__"):
            await edit_res

    return tags


async def setup_forum_tags(forum_channel: discord.ForumChannel) -> tuple[int, int, str | None]:
    """Ensures standard project management tags exist in the given ForumChannel.

    Preserves existing tags and appends missing standard PM tags up to Discord's 20-tag limit.
    Returns (tags_added_count, total_tags_count, error_message_or_None).
    """
    if not isinstance(forum_channel, discord.ForumChannel):
        return 0, 0, "Channel is not a ForumChannel"

    existing_tags = list(getattr(forum_channel, "available_tags", []) or [])
    new_tags_to_add: list[discord.ForumTag] = []

    for tag_def in STANDARD_PM_TAG_DEFINITIONS:
        # Check if an existing tag already covers this category
        already_exists = any(_matches_keyword(t.name, tag_def["keywords"]) for t in existing_tags + new_tags_to_add)
        if not already_exists:
            if len(existing_tags) + len(new_tags_to_add) < 20:
                new_tags_to_add.append(
                    discord.ForumTag(
                        name=tag_def["name"],
                        emoji=tag_def["emoji"],
                        moderated=False,
                    )
                )

    if not new_tags_to_add:
        return 0, len(existing_tags), None

    updated_tag_list = existing_tags + new_tags_to_add
    try:
        await forum_channel.edit(available_tags=updated_tag_list)
        return len(new_tags_to_add), len(updated_tag_list), None
    except discord.Forbidden:
        logger.warning(
            "Missing 'Manage Channels' permission to configure tags in forum #%s (%s)",
            getattr(forum_channel, "name", forum_channel.id),
            forum_channel.id,
        )
        return 0, len(existing_tags), "Bot lacks 'Manage Channels' permission in this forum."
    except Exception as e:
        logger.exception("Failed to update tags in forum channel %s: %s", forum_channel.id, e)
        return 0, len(existing_tags), str(e)


async def ensure_project_tag(forum_channel: discord.ForumChannel, project_name: str) -> str | None:
    """Ensures a per-project tag (e.g. 'Mobile App' with emoji '📁') exists in the ForumChannel.

    Returns an error message on failure, or None on success.
    """
    if not isinstance(forum_channel, discord.ForumChannel):
        return "Channel is not a ForumChannel"

    new_tag = discord.ForumTag(
        name=project_tag_name(project_name),
        emoji=PROJECT_TAG_EMOJI,
        moderated=False,
    )

    existing_tags = list(getattr(forum_channel, "available_tags", []) or [])
    for idx, t in enumerate(existing_tags):
        if _matches_project_tag(t.name, project_name):
            # If the tag has a legacy double-emoji name ('📁 Project Name'), clean it up
            if t.name.startswith(f"{PROJECT_TAG_EMOJI} "):
                tag_emoji = getattr(t, "emoji", None)
                if not isinstance(tag_emoji, (str, discord.Emoji, discord.PartialEmoji)):
                    tag_emoji = PROJECT_TAG_EMOJI
                is_mod = bool(getattr(t, "moderated", False))
                updated_tag = discord.ForumTag(
                    name=project_tag_name(project_name),
                    emoji=tag_emoji,
                    moderated=is_mod,
                )
                existing_tags[idx] = updated_tag
                try:
                    await forum_channel.edit(available_tags=existing_tags)
                    logger.info(
                        "Migrated legacy forum tag '%s' to '%s' in forum #%s",
                        t.name,
                        updated_tag.name,
                        forum_channel.id,
                    )
                except Exception as e:
                    logger.debug("Could not clean up legacy forum tag name: %s", e)
            return None

    if len(existing_tags) >= 20:
        return "Forum is at Discord's 20-tag limit; could not add project tag."

    try:
        await forum_channel.edit(available_tags=[*existing_tags, new_tag])
        logger.info("Added per-project forum tag '%s' (emoji 📁) to forum #%s", project_name, forum_channel.id)
        return None
    except discord.Forbidden:
        return "Bot lacks 'Manage Channels' permission in this forum."
    except Exception as e:
        logger.exception("Failed to add project tag to forum channel %s: %s", forum_channel.id, e)
        return str(e)


async def ensure_pinned_hub_post(
    channel: discord.ForumChannel | discord.TextChannel,
    project_service: ProjectService | None = None,
    squad_service: SquadService | None = None,
    task_service: TaskService | None = None,
    user_service: UserService | None = None,
    project_name: str | None = None,
    auth_service: AuthService | None = None,
) -> tuple[bool, str]:
    """Ensures a pinned Project Management Hub post exists in the given Forum or Text channel."""
    from src.adapters.discord_bot.views.hub_menu import PmHubView, build_hub_welcome_embed
    from src.domain.models import Project

    bound_projects: list[Project] = []
    guild = getattr(channel, "guild", None)
    if project_service and guild and hasattr(guild, "id"):
        try:
            all_projs = await project_service.list_projects(guild.id, include_archived=False)
            bound_projects = [p for p in all_projs if p.discord_channel_id == channel.id]
        except Exception as e:
            logger.warning("Could not list projects for channel %s: %s", channel.id, e)

    if not bound_projects and project_name:
        guild_id = getattr(guild, "id", 0)
        bound_projects = [
            Project(
                guild_id=guild_id,
                name=project_name,
                prefix=project_name[:4].upper().replace(" ", ""),
            )
        ]

    channel_name = getattr(channel, "name", None)
    project_squads_map = {}
    if project_service and bound_projects:
        for bp in bound_projects:
            try:
                project_squads_map[bp.id] = await project_service.list_squads_for_project(bp.id)
            except Exception as e:
                logger.debug("Could not fetch squads for project %s in hub post: %s", bp.id, e)

    embed = build_hub_welcome_embed(
        channel_name=channel_name,
        bound_projects=bound_projects,
        project_squads_map=project_squads_map,
    )

    view = None
    if project_service and task_service:
        if not squad_service and hasattr(task_service, "uow") and hasattr(task_service.uow, "session_factory"):
            try:
                from src.adapters.db.postgres_repo import PostgresSquadRepo
                from src.services.squad_service import SquadService

                squad_service = SquadService(PostgresSquadRepo(task_service.uow.session_factory))
            except Exception as e:
                logger.debug("Could not auto-create squad_service for hub: %s", e)

        if not user_service and hasattr(task_service, "uow") and hasattr(task_service.uow, "session_factory"):
            try:
                from src.adapters.db.postgres_repo import PostgresUserPreferenceRepo
                from src.services.user_service import UserService

                user_service = UserService(PostgresUserPreferenceRepo(task_service.uow.session_factory))
            except Exception as e:
                logger.debug("Could not auto-create user_service for hub: %s", e)

        uow = getattr(task_service, "uow", None)
        sf = getattr(uow, "session_factory", None) or getattr(uow, "_session_factory", None)
        if not auth_service and squad_service and sf:
            try:
                from src.adapters.db.postgres_repo import (
                    PostgresGuildLeadRoleRepository,
                    PostgresGuildLeadUserRepository,
                )
                from src.services.auth_service import AuthService

                lead_role_repo = PostgresGuildLeadRoleRepository(sf)
                lead_user_repo = PostgresGuildLeadUserRepository(sf)
                auth_service = AuthService(
                    project_service,
                    squad_service,
                    guild_lead_role_repo=lead_role_repo,
                    guild_lead_user_repo=lead_user_repo,
                )
            except Exception as e:
                logger.debug("Could not auto-create auth_service for hub: %s", e)

        if squad_service:
            view = PmHubView(
                project_service=project_service,
                squad_service=squad_service,
                task_service=task_service,
                user_service=user_service,
                auth_service=auth_service,
            )

    if isinstance(channel, discord.ForumChannel):
        # 1. Setup standard tags first
        await setup_forum_tags(channel)
        for bp in bound_projects:
            await ensure_project_tag(channel, bp.name)
        if project_name and not any(bp.name == project_name for bp in bound_projects):
            await ensure_project_tag(channel, project_name)

        # 2. Check if a thread with Control Hub / Management Hub already exists
        post_title = f"📌 #{channel.name} • Forum Control Hub" if channel_name else "📌 Forum Control Hub"
        existing_thread = None
        if hasattr(channel, "threads"):
            for t in channel.threads:
                if "Control Hub" in t.name or "Management Hub" in t.name:
                    existing_thread = t
                    break
        if not existing_thread and hasattr(channel, "active_threads"):
            try:
                active_res = channel.active_threads()
                active = await active_res if hasattr(active_res, "__await__") else active_res
                threads_list = getattr(active, "threads", active) or []
                for t in threads_list:
                    if "Control Hub" in t.name or "Management Hub" in t.name:
                        existing_thread = t
                        break
            except Exception:
                pass

        if existing_thread:
            try:
                if hasattr(existing_thread, "edit"):
                    name_changed = getattr(existing_thread, "name", None) != post_title
                    needs_pin = getattr(existing_thread, "pinned", None) is not True
                    if name_changed or needs_pin:
                        edit_kwargs: dict[str, Any] = {"pinned": True}
                        if name_changed:
                            edit_kwargs["name"] = post_title
                        edit_res = existing_thread.edit(**edit_kwargs)
                        if hasattr(edit_res, "__await__"):
                            await edit_res
            except Exception as e:
                logger.warning("Could not edit thread title/pin for %s: %s", getattr(existing_thread, "id", None), e)

            msg_to_edit = getattr(existing_thread, "starter_message", None)
            if not msg_to_edit and hasattr(existing_thread, "fetch_message"):
                try:
                    fetch_res = existing_thread.fetch_message(existing_thread.id)
                    if hasattr(fetch_res, "__await__"):
                        msg_to_edit = await fetch_res
                except Exception:
                    pass
            if msg_to_edit and hasattr(msg_to_edit, "edit"):
                try:
                    edit_res = msg_to_edit.edit(embed=embed, view=view)
                    if hasattr(edit_res, "__await__"):
                        await edit_res
                except Exception as e:
                    logger.warning(
                        "Could not edit starter message in thread %s: %s", getattr(existing_thread, "id", None), e
                    )

            thread_id = getattr(existing_thread, "id", channel.id)
            return True, f"Updated Control Hub in forum #{channel.name} (<#{thread_id}>)."

        try:
            thread_with_msg = await channel.create_thread(
                name=post_title,
                embed=embed,
                view=view,
            )
            thread = thread_with_msg
            if hasattr(thread_with_msg, "thread") and not hasattr(thread_with_msg, "_mock_name"):
                thread = thread_with_msg.thread

            if hasattr(thread, "edit"):
                try:
                    edit_res = thread.edit(pinned=True)
                    if hasattr(edit_res, "__await__"):
                        await edit_res
                except Exception as e:
                    logger.warning("Could not pin forum thread %s: %s", getattr(thread, "id", None), e)
            thread_id = getattr(thread, "id", channel.id)
            return True, f"Created and pinned Control Hub in forum #{channel.name} (<#{thread_id}>)."
        except Exception as e:
            logger.exception("Failed to create pinned forum hub in %s: %s", channel.id, e)
            return False, f"Failed to create forum hub: {e}"

    elif isinstance(channel, discord.TextChannel):
        existing_msg = None
        if hasattr(channel, "pins"):
            try:
                pins_res = channel.pins()
                pins = await pins_res if hasattr(pins_res, "__await__") else pins_res
                for m in pins or []:
                    if getattr(m, "embeds", None) and any(
                        "Control Hub" in (getattr(e, "title", "") or "") for e in m.embeds
                    ):
                        existing_msg = m
                        break
            except Exception:
                pass

        if existing_msg and hasattr(existing_msg, "edit"):
            try:
                edit_res = existing_msg.edit(embed=embed, view=view)
                if hasattr(edit_res, "__await__"):
                    await edit_res
                return True, f"Updated pinned Control Hub in #{channel.name}."
            except Exception as e:
                logger.warning("Could not edit pinned message in text channel %s: %s", channel.id, e)

        try:
            msg = await channel.send(embed=embed, view=view)
            try:
                pin_res = msg.pin()
                if hasattr(pin_res, "__await__"):
                    await pin_res
            except Exception as e:
                logger.warning("Could not pin message in text channel %s: %s", channel.id, e)
            return True, f"Posted and pinned Control Hub in #{channel.name}."
        except Exception as e:
            logger.exception("Failed to post hub in %s: %s", channel.id, e)
            return False, f"Failed to post hub: {e}"

    return False, "Unsupported channel type"
