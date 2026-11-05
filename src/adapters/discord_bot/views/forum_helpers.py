from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import discord

from src.adapters.discord_bot.router import DiscordViewRouter
from src.adapters.discord_bot.views.forum_tags import (
    OVERDUE_KEYWORDS,
    PRIORITY_KEYWORDS,
    PROJECT_TAG_EMOJI,
    STANDARD_PM_TAG_DEFINITIONS,
    STATUS_KEYWORDS,
    UNASSIGNED_KEYWORDS,
    _matches_keyword,
    _matches_project_tag,
    ensure_project_tag,
    project_tag_name,
    prune_archived_project_tags,
    remove_project_tag,
    resolve_forum_tags,
    setup_forum_tags,
    sync_thread_forum_tags,
)

if TYPE_CHECKING:
    from src.services.auth_service import AuthService
    from src.services.project_service import ProjectService
    from src.services.squad_service import SquadService
    from src.services.task_service import TaskService
    from src.services.user_service import UserService

__all__ = [
    "OVERDUE_KEYWORDS",
    "PRIORITY_KEYWORDS",
    "PROJECT_TAG_EMOJI",
    "STANDARD_PM_TAG_DEFINITIONS",
    "STATUS_KEYWORDS",
    "UNASSIGNED_KEYWORDS",
    "_matches_keyword",
    "_matches_project_tag",
    "ensure_pinned_hub_post",
    "ensure_project_tag",
    "project_tag_name",
    "prune_archived_project_tags",
    "remove_project_tag",
    "resolve_forum_tags",
    "setup_forum_tags",
    "sync_thread_forum_tags",
    "unarchive_thread_if_needed",
]

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
            logger.debug("Temporarily unarchived thread %s for safe workspace mutation", getattr(thread, "id", None))
    except Exception as e:
        logger.debug("Failed to unarchive thread %s: %s", getattr(thread, "id", None), e)

    try:
        yield
    finally:
        if was_archived and keep_archived and not getattr(thread, "archived", False):
            try:
                if hasattr(thread, "edit"):
                    edit_res = thread.edit(archived=True)
                    if hasattr(edit_res, "__await__"):
                        await edit_res
                    thread.archived = True
                    logger.debug("Restored archived state for thread %s", getattr(thread, "id", None))
            except Exception as e:
                logger.debug("Failed to restore archived state for thread %s: %s", getattr(thread, "id", None), e)


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
    from src.domain.models import Project

    bound_projects: list[Project] = []
    archived_project_names: list[str] = []
    guild = getattr(channel, "guild", None)
    if project_service and guild and hasattr(guild, "id"):
        try:
            all_projs = await project_service.list_projects(guild.id, include_archived=True)
            bound_projects = [p for p in all_projs if p.discord_channel_id == channel.id and not p.is_archived]
            archived_project_names = [p.name for p in all_projs if p.discord_channel_id == channel.id and p.is_archived]
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

    view = None
    if project_service and task_service:
        uow = getattr(task_service, "uow", None)
        if not squad_service and uow and getattr(uow, "squads", None):
            from src.services.squad_service import SquadService

            squad_service = SquadService(uow.squads, uow=uow)

        if not user_service and uow and getattr(uow, "user_prefs", None):
            from src.services.user_service import UserService

            user_service = UserService(uow.user_prefs, uow=uow)

        if not auth_service and squad_service:
            from src.services.auth_service import AuthService

            auth_service = AuthService(
                project_service,
                squad_service,
                uow=uow,
            )

    if squad_service and project_service and task_service:
        embed, view = DiscordViewRouter.build_hub_view(
            project_service=project_service,
            squad_service=squad_service,
            task_service=task_service,
            user_service=user_service,
            auth_service=auth_service,
            channel_name=channel_name,
            bound_projects=bound_projects,
            project_squads_map=project_squads_map,
        )
    else:
        embed = DiscordViewRouter.build_hub_embed(
            channel_name=channel_name,
            bound_projects=bound_projects,
            project_squads_map=project_squads_map,
        )

    tag_warnings: list[str] = []
    if isinstance(channel, discord.ForumChannel):
        # 1. Prune any orphaned tags belonging to archived projects bound to this channel
        if archived_project_names:
            _pruned, prune_err = await prune_archived_project_tags(channel, archived_project_names)
            if prune_err:
                tag_warnings.append(prune_err)

        # 2. Setup standard tags
        _added, _total, std_err = await setup_forum_tags(channel)
        if std_err:
            tag_warnings.append(std_err)

        # 3. Ensure tags for active projects
        for bp in bound_projects:
            err = await ensure_project_tag(channel, bp.name)
            if err:
                tag_warnings.append(err)
        if (
            project_name
            and not any(bp.name == project_name for bp in bound_projects)
            and project_name not in archived_project_names
        ):
            err = await ensure_project_tag(channel, project_name)
            if err:
                tag_warnings.append(err)

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
            warn_suffix = f" (Warning: {'; '.join(tag_warnings)})" if tag_warnings else ""
            return True, f"Updated Control Hub in forum #{channel.name} (<#{thread_id}>).{warn_suffix}"

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
            warn_suffix = f" (Warning: {'; '.join(tag_warnings)})" if tag_warnings else ""
            return True, f"Created and pinned Control Hub in forum #{channel.name} (<#{thread_id}>).{warn_suffix}"
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
