"""Event handlers for task lifecycle notifications (created, updated, unblocked)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

import discord

from src.adapters.discord_bot.views.forum_helpers import resolve_forum_tags
from src.adapters.discord_bot.views.task_buttons import TaskLinkButtonView
from src.adapters.discord_bot.views.task_embed import resolve_task_jump_url
from src.domain.enums import PriorityLevel

if TYPE_CHECKING:
    from src.domain.models import Task

logger = logging.getLogger("dgg_pm.notifier_task_handlers")

NOTIFICATION_FOOTER = "Control notifications via /pm settings"


class NotifierTaskHandlersMixin:
    """Mixin providing task created, updated, and unblocked handlers for DiscordNotifier."""

    bot: discord.Client
    task_service: Any
    workspace: Any

    async def _resolve_task(self, payload: dict) -> Task:
        raise NotImplementedError

    async def _notify_user(
        self,
        *,
        guild_id: int | None,
        user_id: int,
        embed: discord.Embed,
        thread: discord.Thread | None,
        mention_text: str | None = None,
        view: discord.ui.View | None = None,
    ) -> bool:
        raise NotImplementedError

    async def _handle_task_created(self, payload: dict) -> None:
        short_id = payload.get("short_id", "")
        title = payload.get("title", "")
        assignee_id = payload.get("assignee_discord_id")
        creator_id = payload.get("creator_discord_id")
        guild_id = payload.get("guild_id")
        thread_id = payload.get("discord_thread_id")
        message_id = payload.get("discord_message_id")
        watchers: list[int] = payload.get("watchers", [])

        if not thread_id and self.task_service:
            try:
                task = None
                task_id_raw = payload.get("task_id")
                if task_id_raw:
                    try:
                        task = await self.task_service.get_by_id(UUID(str(task_id_raw)))
                    except (ValueError, TypeError):
                        task = None
                if not task and guild_id and short_id:
                    task = await self.task_service.get_by_short_id(guild_id, short_id)
                if task:
                    if not guild_id and task.guild_id:
                        guild_id = task.guild_id
                    if not thread_id and task.discord_thread_id:
                        thread_id = task.discord_thread_id
                    if not message_id and task.discord_message_id:
                        message_id = task.discord_message_id
            except Exception as e:
                logger.debug("Could not resolve task location from task_service for task_created: %s", e)

        thread: discord.Thread | None = None
        if thread_id:
            try:
                chan = self.bot.get_channel(thread_id) or await self.bot.fetch_channel(thread_id)
                if isinstance(chan, discord.Thread):
                    thread = chan
            except Exception:
                thread = None

        jump_url = resolve_task_jump_url(guild_id, thread_id, message_id)
        view = TaskLinkButtonView(jump_url) if jump_url else None

        if assignee_id and assignee_id != creator_id:
            desc = f"<@{creator_id}> assigned you a new task: **{title}**"
            embed = discord.Embed(
                title=f"New Task Assigned: [{short_id}]",
                url=jump_url,
                description=desc,
                color=discord.Color.blue(),
            )
            embed.set_footer(text=NOTIFICATION_FOOTER)

            await self._notify_user(
                guild_id=guild_id,
                user_id=assignee_id,
                embed=embed,
                thread=thread,
                mention_text=f"<@{assignee_id}> You have been assigned to task **[{short_id}] {title}**!",
                view=view,
            )

        # Notify watchers added during task creation
        watcher_recipients = set(watchers) - {creator_id}
        if assignee_id:
            watcher_recipients.discard(assignee_id)
        if watcher_recipients:
            desc = f"<@{creator_id}> added you as a watcher to task **{title}**."
            watcher_embed = discord.Embed(
                title=f"Added as Watcher: [{short_id}]",
                url=jump_url,
                description=desc,
                color=discord.Color.blue(),
            )
            watcher_embed.set_footer(text=NOTIFICATION_FOOTER)
            for w_id in watcher_recipients:
                await self._notify_user(
                    guild_id=guild_id,
                    user_id=w_id,
                    embed=watcher_embed,
                    thread=thread,
                    mention_text=f"<@{w_id}> You were added as a watcher on task **[{short_id}] {title}**",
                    view=view,
                )

    async def _handle_task_updated(self, payload: dict) -> None:
        short_id = payload.get("short_id", "")
        title = payload.get("title", "")
        guild_id = payload.get("guild_id")
        actor_id = payload.get("actor_discord_id")
        thread_id = payload.get("discord_thread_id")
        update_type = payload.get("update_type", "details")
        watchers: list[int] = payload.get("watchers", [])
        assignee_id = payload.get("assignee_discord_id")

        added_watchers: set[int] = set()
        if update_type == "assignee" or ("new_assignee_id" in payload or "old_assignee_id" in payload):
            new_assignee = payload.get("new_assignee_id")
            old_assignee = payload.get("old_assignee_id")
            if new_assignee:
                thread_msg = f"**Assignee Updated:** Assigned to <@{new_assignee}> by <@{actor_id}>"
                embed_desc = f"<@{actor_id}> assigned this task to <@{new_assignee}>."
            else:
                thread_msg = f"**Assignee Updated:** Unassigned by <@{actor_id}>"
                embed_desc = f"<@{actor_id}> removed the assignee from this task."

            embed_title = f"Task Assignment: [{short_id}] {title}"
            embed_color = discord.Color.blue()
            recipients = set(watchers)
            if new_assignee:
                recipients.add(new_assignee)
            if old_assignee:
                recipients.add(old_assignee)

        elif update_type == "priority" or ("old_priority" in payload and "new_priority" in payload):
            old_prio = payload.get("old_priority", "")
            new_prio = payload.get("new_priority", "")
            thread_msg = f"**Priority Updated:** `{old_prio}` ➔ **`{new_prio}`** by <@{actor_id}>"
            embed_desc = f"Priority changed from `{old_prio}` to **`{new_prio}`** by <@{actor_id}>."
            embed_title = f"Priority Changed: [{short_id}] {title}"
            embed_color = discord.Color.gold()
            recipients = set(watchers)
            if assignee_id:
                recipients.add(assignee_id)

        else:
            diffs = payload.get("diffs", [])
            if diffs:
                formatted_lines: list[str] = []
                for d in diffs:
                    field = d.get("field")
                    if field == "title":
                        formatted_lines.append(f"Title: `{d.get('old_value')}` ➔ **`{d.get('new_value')}`**")
                    elif field == "body":
                        formatted_lines.append(
                            "Description removed" if d.get("new_value") is None else "Description updated"
                        )
                    elif field == "due_at":
                        new_due = d.get("new_value")
                        if new_due is None:
                            formatted_lines.append("Due date removed")
                        else:
                            formatted_lines.append(f"Due date set to `{new_due}`")
                    elif field == "watchers":
                        user_ids = d.get("user_ids", [])
                        mentions = ", ".join(f"<@{u}>" for u in user_ids)
                        action = d.get("action", "updated")
                        formatted_lines.append(f"{action.capitalize()} watchers: {mentions}")
                    else:
                        formatted_lines.append(str(d))
                change_text = "\n".join(f"• {c}" for c in formatted_lines)
            else:
                changes = payload.get("changes", [])
                change_text = "\n".join(f"• {c}" for c in changes) if changes else "Task details were updated."
            thread_msg = f"**Task Updated by <@{actor_id}>:**\n{change_text}"
            embed_desc = f"<@{actor_id}> updated task details:\n{change_text}"
            embed_title = f"Task Updated: [{short_id}] {title}"
            embed_color = discord.Color.blue()
            old_watchers = payload.get("old_watchers", [])
            added_watchers = set(watchers) - set(old_watchers)
            recipients = set(watchers) | set(old_watchers)
            if assignee_id:
                recipients.add(assignee_id)

        thread: discord.Thread | None = None
        task = await self._resolve_task(payload)
        if not guild_id and task.guild_id:
            guild_id = task.guild_id
        if not thread_id and task.discord_thread_id:
            thread_id = task.discord_thread_id
        message_id = payload.get("discord_message_id") or task.discord_message_id

        if self.workspace and thread_id:
            await self.workspace.post_activity(task, content=thread_msg, rearchive_if_completed=False)
            await self.workspace.sync_workspace(task)
            chan = self.bot.get_channel(thread_id)
            if isinstance(chan, discord.Thread):
                thread = chan
        elif thread_id:
            try:
                chan = self.bot.get_channel(thread_id) or await self.bot.fetch_channel(thread_id)
                if isinstance(chan, discord.Thread):
                    thread = chan
                    if update_type == "priority" and isinstance(thread.parent, discord.ForumChannel):
                        try:
                            prio_val = PriorityLevel(payload.get("new_priority", "normal"))
                            edit_kwargs = {
                                "applied_tags": resolve_forum_tags(
                                    thread.parent,
                                    priority=prio_val,
                                    existing_tags=getattr(thread, "applied_tags", None),
                                )
                            }
                            await thread.edit(**edit_kwargs)
                        except Exception as err:
                            logger.debug("Could not edit forum thread tags for priority update: %s", err)

                    is_completed = payload.get("status") == "completed" or payload.get("is_completed", False)
                    was_archived = getattr(thread, "archived", False)
                    await thread.send(thread_msg)
                    if was_archived or is_completed:
                        try:
                            await thread.edit(archived=True)
                        except Exception:
                            pass
            except Exception as e:
                logger.warning("Failed to post task update into thread %s: %s", thread_id, e)

        # Notify watchers & assignee via DM / thread
        recipients.discard(actor_id)
        if recipients:
            message_id = payload.get("discord_message_id")
            if not thread_id and self.task_service:
                try:
                    task_obj = None
                    task_id_raw = payload.get("task_id")
                    if task_id_raw:
                        try:
                            task_obj = await self.task_service.get_by_id(UUID(str(task_id_raw)))
                        except (ValueError, TypeError):
                            task_obj = None
                    if not task_obj and guild_id and short_id:
                        task_obj = await self.task_service.get_by_short_id(guild_id, short_id)
                    if task_obj:
                        if not guild_id and task_obj.guild_id:
                            guild_id = task_obj.guild_id
                        if not thread_id and task_obj.discord_thread_id:
                            thread_id = task_obj.discord_thread_id
                        if not message_id and task_obj.discord_message_id:
                            message_id = task_obj.discord_message_id
                except Exception as e:
                    logger.debug("Could not resolve task location from task_service for task_updated: %s", e)

            jump_url = resolve_task_jump_url(guild_id, thread_id, message_id)
            view = TaskLinkButtonView(jump_url) if jump_url else None

            embed = discord.Embed(
                title=embed_title,
                url=jump_url,
                description=embed_desc,
                color=embed_color,
            )
            embed.set_footer(text=NOTIFICATION_FOOTER)
            for uid in recipients:
                if uid in added_watchers:
                    user_mention = f"👀 <@{uid}> You were added as a watcher on task **[{short_id}] {title}**"
                else:
                    user_mention = f"🔔 <@{uid}> **[{short_id}] {title}** was updated by <@{actor_id}>"
                await self._notify_user(
                    guild_id=guild_id,
                    user_id=uid,
                    embed=embed,
                    thread=thread,
                    mention_text=user_mention,
                    view=view,
                )

    async def _handle_task_unblocked(self, payload: dict) -> None:
        short_id = payload.get("short_id", "")
        title = payload.get("title", "")
        guild_id = payload.get("guild_id")
        actor_id = payload.get("actor_discord_id")
        thread_id = payload.get("discord_thread_id")
        message_id = payload.get("discord_message_id")
        watchers: list[int] = payload.get("watchers", [])
        assignee_id = payload.get("assignee_discord_id")
        prereqs_raw: list[dict[str, Any]] = payload.get("prerequisites") or []
        unblocked_by_short_id = payload.get("unblocked_by_short_id", "")
        unblocked_by_title = payload.get("unblocked_by_title", "")

        thread_msg = (
            f"🎉 All prerequisite blockers for **[{short_id}] {title}** have completed! "
            f"You are now clear to begin work."
        )

        thread: discord.Thread | None = None
        task = await self._resolve_task(payload)
        if not guild_id and task.guild_id:
            guild_id = task.guild_id
        if not thread_id and task.discord_thread_id:
            thread_id = task.discord_thread_id
        if not message_id and task.discord_message_id:
            message_id = task.discord_message_id

        if self.workspace and thread_id:
            await self.workspace.post_activity(task, content=thread_msg, rearchive_if_completed=False)
            await self.workspace.sync_workspace(task)
            chan = self.bot.get_channel(thread_id)
            if isinstance(chan, discord.Thread):
                thread = chan
        elif thread_id:
            try:
                chan = self.bot.get_channel(thread_id) or await self.bot.fetch_channel(thread_id)
                if isinstance(chan, discord.Thread):
                    thread = chan
                    await thread.send(thread_msg)
            except Exception as e:
                logger.warning("Failed to post unblock activity to thread %s: %s", thread_id, e)

        recipients = set(watchers)
        if assignee_id:
            recipients.add(assignee_id)
        if actor_id:
            recipients.discard(actor_id)

        if recipients:
            jump_url = resolve_task_jump_url(guild_id, thread_id, message_id)
            view = TaskLinkButtonView(jump_url) if jump_url else None

            desc = (
                f"🎉 All prerequisite blockers for **[{short_id}] {title}** have completed! "
                f"You are now clear to begin work."
            )

            embed = discord.Embed(
                title=f"Task Unblocked: [{short_id}] {title}",
                url=jump_url,
                description=desc,
                color=discord.Color.green(),
            )
            embed.set_footer(text=NOTIFICATION_FOOTER)

            if prereqs_raw:
                prereq_lines = [
                    f"• 🟢 **[{p.get('short_id')}]** {p.get('title')}" for p in prereqs_raw if p.get("short_id")
                ]
                if prereq_lines:
                    embed.add_field(
                        name="Resolved Prerequisites",
                        value="\n".join(prereq_lines),
                        inline=False,
                    )
            elif unblocked_by_short_id:
                embed.add_field(
                    name="Resolved Prerequisite",
                    value=f"• 🟢 **[{unblocked_by_short_id}]** {unblocked_by_title}".strip(),
                    inline=False,
                )

            for uid in recipients:
                await self._notify_user(
                    guild_id=guild_id,
                    user_id=uid,
                    embed=embed,
                    thread=thread,
                    mention_text=(
                        f"<@{uid}> 🎉 All prerequisite blockers for **[{short_id}] {title}** "
                        f"have completed! You are now clear to begin work."
                    ),
                    view=view,
                )
