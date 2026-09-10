from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import discord

from src.adapters.discord_bot.views.forum_helpers import resolve_forum_tags
from src.adapters.discord_bot.workspace_protocol import ITaskDiscordWorkspace
from src.domain.enums import EventType, NotificationPreference, PriorityLevel, TaskStatus
from src.domain.models import OutboxEvent, Task
from src.ports.notifier import INotificationDispatcher

if TYPE_CHECKING:
    from src.services.user_service import UserService

logger = logging.getLogger("dgg_pm.discord_notifier")

NOTIFICATION_FOOTER = "Control notifications via /pm settings"


class DiscordNotifier(INotificationDispatcher):
    def __init__(
        self,
        bot: discord.Client,
        user_service: UserService | None = None,
        workspace: ITaskDiscordWorkspace | None = None,
    ):
        self.bot = bot
        self.user_service = user_service
        self.workspace = workspace

    def _reconstruct_task(self, payload: dict) -> Task:
        """Reconstructs a transient Task domain model from outbox payload for workspace synchronization."""
        try:
            task_id = UUID(payload["task_id"]) if "task_id" in payload else UUID(int=0)
        except (ValueError, TypeError, AttributeError):
            task_id = UUID(int=0)
        guild_id = int(payload.get("guild_id") or 0)
        try:
            project_id = UUID(str(payload["project_id"])) if payload.get("project_id") else uuid4()
        except (ValueError, TypeError, AttributeError):
            project_id = uuid4()
        short_id = payload.get("short_id", "")
        title = payload.get("title", "")
        status_val = payload.get("new_status") or payload.get("status") or "notStarted"
        status = TaskStatus(status_val) if status_val in TaskStatus._value2member_map_ else TaskStatus.NOT_STARTED
        prio_val = payload.get("new_priority") or payload.get("priority") or "normal"
        priority = PriorityLevel(prio_val) if prio_val in PriorityLevel._value2member_map_ else PriorityLevel.NORMAL
        creator_id = int(payload.get("creator_discord_id") or payload.get("actor_discord_id") or 0)
        assignee_id = payload.get("assignee_discord_id") or payload.get("new_assignee_id")
        thread_id = payload.get("discord_thread_id")
        msg_id = payload.get("discord_message_id")
        watchers = payload.get("watchers") or []

        return Task(
            id=task_id,
            guild_id=guild_id,
            project_id=project_id,
            short_id=short_id,
            title=title,
            status=status,
            priority=priority,
            creator_discord_id=creator_id,
            assignee_discord_id=assignee_id,
            watchers=watchers,
            discord_thread_id=thread_id,
            discord_message_id=msg_id,
        )

    async def _get_pref(self, guild_id: int | None, user_id: int) -> NotificationPreference:
        if not self.user_service or not guild_id:
            return NotificationPreference.DM
        try:
            return await self.user_service.get_preference(guild_id, user_id)
        except Exception as e:
            logger.debug("Could not fetch user pref for %s: %s", user_id, e)
            return NotificationPreference.DM

    async def _send_dm(self, user_id: int, embed: discord.Embed) -> bool:
        """Helper to send a Direct Message to a Discord user."""
        try:
            if embed and not (embed.footer and embed.footer.text):
                embed.set_footer(text=NOTIFICATION_FOOTER)
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            if user:
                await user.send(embed=embed)
                return True
        except discord.Forbidden:
            logger.warning("Cannot send DM to user %s (DMs closed or user blocked bot).", user_id)
        except Exception as e:
            logger.exception("Failed to send DM to user %s: %s", user_id, e)
        return False

    async def _notify_user(
        self,
        *,
        guild_id: int | None,
        user_id: int,
        embed: discord.Embed,
        thread: discord.Thread | None = None,
        mention_text: str | None = None,
    ) -> None:
        if embed and not (embed.footer and embed.footer.text):
            embed.set_footer(text=NOTIFICATION_FOOTER)

        pref = await self._get_pref(guild_id, user_id)
        if pref == NotificationPreference.NONE:
            return

        dm_success = False
        if pref in (NotificationPreference.DM, NotificationPreference.BOTH):
            dm_success = await self._send_dm(user_id, embed)

        # If user wants channel mentions, OR if DM failed (e.g. DMs closed) and thread exists
        if pref in (NotificationPreference.CHANNEL, NotificationPreference.BOTH) or (
            pref == NotificationPreference.DM and not dm_success
        ):
            if thread:
                try:
                    text = mention_text or f"🔔 <@{user_id}>"
                    await thread.send(content=text, embed=embed)
                except Exception as e:
                    logger.warning("Failed to send in-thread notification to %s: %s", user_id, e)

    async def dispatch_event(self, event: OutboxEvent) -> None:
        p = event.payload
        logger.info("Dispatching outbox event %s of type %s", event.id, event.event_type)

        if event.event_type == EventType.TASK_DUE_REMINDER:
            await self._handle_due_reminder(p)
        elif event.event_type == EventType.TASK_STATUS_CHANGED:
            await self._handle_status_changed(p)
        elif event.event_type == EventType.TASK_NOTE_ADDED:
            await self._handle_note_added(p)
        elif event.event_type == EventType.TASK_CREATED:
            await self._handle_task_created(p)
        elif event.event_type == EventType.TASK_UPDATED:
            await self._handle_task_updated(p)

    async def _handle_due_reminder(self, payload: dict) -> None:
        assignee_id = payload.get("assignee_discord_id")
        if not assignee_id:
            return

        guild_id = payload.get("guild_id")
        thread_id = payload.get("discord_thread_id")
        reminder_type = payload.get("reminder_type", "due")
        short_id = payload.get("short_id", "")
        title = payload.get("title", "")

        reminder_labels = {
            "24h": "due in 24 hours",
            "1h": "due in 1 hour",
            "due": "due right now",
        }
        label = reminder_labels.get(reminder_type, "approaching deadline")

        embed = discord.Embed(
            title=f"Task Deadline Reminder: [{short_id}]",
            description=f"Your assigned task **[{short_id}] {title}** is **{label}**.",
            color=discord.Color.red() if reminder_type == "due" else discord.Color.gold(),
        )
        embed.set_footer(text=NOTIFICATION_FOOTER)
        embed.add_field(name="Task Title", value=title, inline=True)
        if payload.get("due_at"):
            embed.add_field(name="Due Timestamp", value=payload["due_at"], inline=True)

        thread = None
        if thread_id:
            try:
                chan = self.bot.get_channel(thread_id) or await self.bot.fetch_channel(thread_id)
                if isinstance(chan, discord.Thread):
                    thread = chan
            except Exception:
                pass

        await self._notify_user(
            guild_id=guild_id,
            user_id=assignee_id,
            embed=embed,
            thread=thread,
            mention_text=f"<@{assignee_id}> **Task Deadline Reminder:** [{short_id}] {title} is **{label}**!",
        )

    async def _handle_status_changed(self, payload: dict) -> None:
        short_id = payload.get("short_id", "")
        title = payload.get("title", "")
        guild_id = payload.get("guild_id")
        old_status = payload.get("old_status", "")
        new_status = payload.get("new_status", "")
        actor_id = payload.get("actor_discord_id")
        notes = payload.get("notes")
        thread_id = payload.get("discord_thread_id")
        watchers: list[int] = payload.get("watchers", [])
        assignee_id = payload.get("assignee_discord_id")

        thread: discord.Thread | None = None
        task = self._reconstruct_task(payload)

        if self.workspace and thread_id:
            msg = f"**Status Updated:** `{old_status}` ➔ **`{new_status}`** by <@{actor_id}>"
            if notes:
                msg += f"\n> {notes}"
            await self.workspace.post_activity(task, content=msg)
            await self.workspace.sync_workspace(task)
            chan = self.bot.get_channel(thread_id)
            if isinstance(chan, discord.Thread):
                thread = chan
        elif thread_id:
            message_id = payload.get("discord_message_id")
            try:
                chan = self.bot.get_channel(thread_id) or await self.bot.fetch_channel(thread_id)
                if isinstance(chan, discord.Thread):
                    thread = chan
                    msg = f"**Status Updated:** `{old_status}` ➔ **`{new_status}`** by <@{actor_id}>"
                    if notes:
                        msg += f"\n> {notes}"
                    await thread.send(msg)

                    if message_id:
                        try:
                            root_msg = None
                            if isinstance(thread.parent, discord.ForumChannel):
                                root_msg = thread.starter_message or await thread.fetch_message(message_id)
                            elif thread.parent:
                                root_msg = await thread.parent.fetch_message(message_id)

                            if root_msg and root_msg.embeds:
                                old_embed = root_msg.embeds[0]
                                updated_embed = discord.Embed(
                                    title=old_embed.title,
                                    description=old_embed.description,
                                    color=discord.Color.green() if new_status == "completed" else discord.Color.blue(),
                                )
                                for f in old_embed.fields:
                                    if f.name == "Status":
                                        status_val = (
                                            "🟢 Completed"
                                            if new_status == "completed"
                                            else ("🟡 In Progress" if new_status == "inProgress" else "⚪ Not Started")
                                        )
                                        updated_embed.add_field(name="Status", value=status_val, inline=f.inline)
                                    else:
                                        updated_embed.add_field(name=f.name, value=f.value, inline=f.inline)

                                if old_embed.footer and old_embed.footer.text:
                                    updated_embed.set_footer(text=old_embed.footer.text)

                                await root_msg.edit(embed=updated_embed)
                        except Exception as edit_err:
                            logger.warning(
                                "Could not update root task embed in thread parent %s: %s", thread.id, edit_err
                            )

                    edit_kwargs: dict[str, object] = {}
                    if isinstance(thread.parent, discord.ForumChannel):
                        prio_val = None
                        if payload.get("priority"):
                            try:
                                prio_val = PriorityLevel(payload["priority"])
                            except ValueError:
                                pass
                        edit_kwargs["applied_tags"] = resolve_forum_tags(
                            thread.parent,
                            status=TaskStatus(new_status),
                            priority=prio_val,
                            existing_tags=getattr(thread, "applied_tags", None),
                        )

                    if new_status == "completed":
                        edit_kwargs["archived"] = True
                    elif thread.archived:
                        edit_kwargs["archived"] = False

                    if edit_kwargs:
                        try:
                            await thread.edit(**edit_kwargs)
                        except Exception as archive_err:
                            logger.debug("Could not edit thread state after status update: %s", archive_err)
            except Exception as e:
                logger.warning("Failed to post status update into thread %s: %s", thread_id, e)

        # Notify watchers and assignee
        recipients = set(watchers)
        if assignee_id and assignee_id != actor_id:
            recipients.add(assignee_id)
        recipients.discard(actor_id)  # Don't notify the person who triggered the change

        if recipients:
            embed = discord.Embed(
                title=f"Task Update: [{short_id}] {title}",
                description=f"Status changed from `{old_status}` to **`{new_status}`** by <@{actor_id}>.",
                color=discord.Color.green() if new_status == "completed" else discord.Color.blue(),
            )
            embed.set_footer(text=NOTIFICATION_FOOTER)
            if notes:
                embed.add_field(name="Notes", value=notes, inline=False)

            for uid in recipients:
                await self._notify_user(
                    guild_id=guild_id,
                    user_id=uid,
                    embed=embed,
                    thread=thread,
                    mention_text=f"<@{uid}> Task **[{short_id}] {title}** status updated to **`{new_status}`**",
                )

    async def _handle_note_added(self, payload: dict) -> None:
        short_id = payload.get("short_id", "")
        title = payload.get("title", "")
        guild_id = payload.get("guild_id")
        actor_id = payload.get("actor_discord_id")
        note = payload.get("note", "")
        thread_id = payload.get("discord_thread_id")
        watchers: list[int] = payload.get("watchers", [])
        assignee_id = payload.get("assignee_discord_id")

        thread: discord.Thread | None = None
        task = self._reconstruct_task(payload)

        if self.workspace and thread_id:
            await self.workspace.post_activity(task, content=f"**Note added by <@{actor_id}>:**\n> {note}")
            chan = self.bot.get_channel(thread_id)
            if isinstance(chan, discord.Thread):
                thread = chan
        elif thread_id:
            is_completed = payload.get("status") == "completed" or payload.get("is_completed", False)
            try:
                chan = self.bot.get_channel(thread_id) or await self.bot.fetch_channel(thread_id)
                if isinstance(chan, discord.Thread):
                    thread = chan
                    was_archived = getattr(thread, "archived", False)
                    await thread.send(f"**Note added by <@{actor_id}>:**\n> {note}")
                    if was_archived or is_completed:
                        try:
                            await thread.edit(archived=True)
                        except Exception:
                            pass
            except Exception as e:
                logger.warning("Failed to post note to thread %s: %s", thread_id, e)

        # Notify assignee & watchers
        recipients = set(watchers)
        if assignee_id and assignee_id != actor_id:
            recipients.add(assignee_id)
        recipients.discard(actor_id)

        if recipients:
            embed = discord.Embed(
                title=f"New Note: [{short_id}] {title}",
                description=f"<@{actor_id}> added a note:\n> {note}",
                color=discord.Color.blue(),
            )
            embed.set_footer(text=NOTIFICATION_FOOTER)
            for uid in recipients:
                await self._notify_user(
                    guild_id=guild_id,
                    user_id=uid,
                    embed=embed,
                    thread=thread,
                    mention_text=f"<@{uid}> New note on **[{short_id}] {title}**",
                )

    async def _handle_task_created(self, payload: dict) -> None:
        assignee_id = payload.get("assignee_discord_id")
        creator_id = payload.get("creator_discord_id")
        guild_id = payload.get("guild_id")
        thread_id = payload.get("discord_thread_id")
        watchers: list[int] = payload.get("watchers", [])

        thread: discord.Thread | None = None
        if thread_id:
            try:
                chan = self.bot.get_channel(thread_id) or await self.bot.fetch_channel(thread_id)
                if isinstance(chan, discord.Thread):
                    thread = chan
            except Exception:
                pass

        short_id = payload.get("short_id", "")
        title = payload.get("title", "")

        if assignee_id and assignee_id != creator_id:
            embed = discord.Embed(
                title=f"New Task Assigned: [{short_id}]",
                description=f"<@{creator_id}> assigned you a new task: **{title}**",
                color=discord.Color.blue(),
            )
            embed.set_footer(text=NOTIFICATION_FOOTER)

            await self._notify_user(
                guild_id=guild_id,
                user_id=assignee_id,
                embed=embed,
                thread=thread,
                mention_text=f"<@{assignee_id}> You have been assigned to task **[{short_id}] {title}**!",
            )

        # Notify watchers added during task creation
        watcher_recipients = set(watchers) - {creator_id}
        if assignee_id:
            watcher_recipients.discard(assignee_id)
        if watcher_recipients:
            watcher_embed = discord.Embed(
                title=f"Added as Watcher: [{short_id}]",
                description=f"<@{creator_id}> added you as a watcher to task **{title}**.",
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
        task = self._reconstruct_task(payload)

        if self.workspace and thread_id:
            await self.workspace.post_activity(task, content=thread_msg)
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
                logger.warning("Failed to post update to thread %s: %s", thread_id, e)

        # Notify watchers & assignee via DM / thread
        recipients.discard(actor_id)
        if recipients:
            embed = discord.Embed(
                title=embed_title,
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
                )
