from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from src.domain.enums import PriorityLevel, TaskStatus

if TYPE_CHECKING:
    from src.domain.models import Task

logger = logging.getLogger("dgg_pm.views.forum_tags")

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
        try:
            forum_channel.available_tags = updated_tag_list
        except Exception:
            pass
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
                    try:
                        forum_channel.available_tags = existing_tags
                    except Exception:
                        pass
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
        new_list = [*existing_tags, new_tag]
        await forum_channel.edit(available_tags=new_list)
        try:
            forum_channel.available_tags = new_list
        except Exception:
            pass
        logger.info("Added per-project forum tag '%s' (emoji 📁) to forum #%s", project_name, forum_channel.id)
        return None
    except discord.Forbidden:
        return "Bot lacks 'Manage Channels' permission in this forum."
    except Exception as e:
        logger.exception("Failed to add project tag to forum channel %s: %s", forum_channel.id, e)
        return str(e)


async def remove_project_tag(forum_channel: discord.ForumChannel, project_name: str) -> tuple[bool, str | None]:
    """Removes the per-project forum tag matching `project_name` from the ForumChannel.

    Returns (removed_bool, error_message_or_None).
    """
    if not isinstance(forum_channel, discord.ForumChannel):
        return False, "Channel is not a ForumChannel"

    existing_tags = list(getattr(forum_channel, "available_tags", []) or [])
    new_tags = [t for t in existing_tags if not _matches_project_tag(t.name, project_name)]

    if len(new_tags) == len(existing_tags):
        return False, None

    try:
        await forum_channel.edit(available_tags=new_tags)
        try:
            forum_channel.available_tags = new_tags
        except Exception:
            pass
        logger.info("Removed per-project forum tag for '%s' from forum #%s", project_name, forum_channel.id)
        return True, None
    except discord.Forbidden:
        logger.warning(
            "Missing 'Manage Channels' permission to remove tag in forum #%s (%s)",
            getattr(forum_channel, "name", forum_channel.id),
            forum_channel.id,
        )
        return False, "Bot lacks 'Manage Channels' permission in this forum."
    except Exception as e:
        logger.exception("Failed to remove project tag from forum channel %s: %s", forum_channel.id, e)
        return False, str(e)


async def prune_archived_project_tags(
    forum_channel: discord.ForumChannel,
    archived_project_names: list[str] | set[str],
) -> tuple[int, str | None]:
    """Removes forum tags belonging to known archived projects from the ForumChannel.

    Returns (tags_pruned_count, error_message_or_None).
    """
    if not isinstance(forum_channel, discord.ForumChannel):
        return 0, "Channel is not a ForumChannel"

    if not archived_project_names:
        return 0, None

    archived_set = {name.strip().lower() for name in archived_project_names if name}
    if not archived_set:
        return 0, None

    existing_tags = list(getattr(forum_channel, "available_tags", []) or [])
    new_tags = []
    pruned_count = 0

    for t in existing_tags:
        is_archived = any(_matches_project_tag(t.name, arch_name) for arch_name in archived_project_names)
        if is_archived:
            pruned_count += 1
        else:
            new_tags.append(t)

    if pruned_count == 0:
        return 0, None

    try:
        await forum_channel.edit(available_tags=new_tags)
        try:
            forum_channel.available_tags = new_tags
        except Exception:
            pass
        logger.info("Pruned %d archived project tag(s) from forum #%s", pruned_count, forum_channel.id)
        return pruned_count, None
    except discord.Forbidden:
        logger.warning(
            "Missing 'Manage Channels' permission to prune tags in forum #%s (%s)",
            getattr(forum_channel, "name", forum_channel.id),
            forum_channel.id,
        )
        return 0, "Bot lacks 'Manage Channels' permission in this forum."
    except Exception as e:
        logger.exception("Failed to prune archived project tags from forum channel %s: %s", forum_channel.id, e)
        return 0, str(e)
