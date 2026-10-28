from __future__ import annotations

import logging
import re
from typing import Any

import discord
from discord import app_commands

from src.adapters.discord_bot.cogs.autocomplete import AutocompleteMixin
from src.adapters.discord_bot.cogs.task_actions_cog import TaskActionsMixin
from src.adapters.discord_bot.error_handler import send_interaction_error
from src.adapters.discord_bot.router import DiscordViewRouter
from src.adapters.discord_bot.views.task_embed import (
    build_task_embed,
    build_task_history_embed,
)
from src.adapters.discord_bot.views.task_list_view import TaskListView, build_page_embed
from src.adapters.discord_bot.views.task_modals import parse_natural_date
from src.domain.enums import PriorityLevel, TaskStatus
from src.domain.models import Task

logger = logging.getLogger("dgg_pm.cogs.task")


def extract_user_ids(text: str | None) -> list[int]:
    """Extracts Discord user snowflake IDs from mentions (<@123456789>) or raw IDs."""
    if not text:
        return []
    ids = re.findall(r"\d{4,20}", text)
    return [int(uid) for uid in set(ids)]


def extract_short_ids(text: str | None) -> list[str]:
    """Extracts task short IDs (e.g. 'INF-1', 'PROJ-12') from a delimiter-separated string."""
    if not text:
        return []
    tokens = re.split(r"[\s,;]+", text.strip())
    results: list[str] = []
    for token in tokens:
        clean = token.strip().strip("`#[]()").upper()
        if clean and "-" in clean and len(clean) <= 30:
            if clean not in results:
                results.append(clean)
    return results


project_autocomplete = AutocompleteMixin.project_autocomplete
task_autocomplete = AutocompleteMixin.task_autocomplete


class TaskCommandsMixin(TaskActionsMixin):
    """Task lifecycle, management, and tracking slash commands."""

    task_group = TaskActionsMixin.task_group

    @task_group.command(name="create", description="Create a new task and thread in a project container.")
    @app_commands.describe(
        project_name="Target project name",
        title="Brief task summary",
        assignee="Discord member to assign (must hold squad role)",
        due="Natural due date (e.g. 'tomorrow 5pm')",
        priority="Task priority level",
        cc="Watcher members to notify (@mentions or IDs)",
        description="Optional detailed markdown description",
        prereqs="Prerequisite tasks (comma/space-separated short IDs, e.g. 'INF-1, INF-2')",
        depends_on="Alias for prereqs (supports autocomplete search)",
    )
    @app_commands.choices(
        priority=[
            app_commands.Choice(name="High", value="high"),
            app_commands.Choice(name="Normal", value="normal"),
            app_commands.Choice(name="Low", value="low"),
        ]
    )
    @app_commands.autocomplete(
        project_name=project_autocomplete,
        prereqs=task_autocomplete,
        depends_on=task_autocomplete,
    )
    async def task_create(
        self,
        interaction: discord.Interaction,
        project_name: str,
        title: str,
        assignee: discord.Member | None = None,
        due: str | None = None,
        priority: str = "normal",
        cc: str | None = None,
        description: str | None = None,
        prereqs: str | None = None,
        depends_on: str | None = None,
    ) -> None:
        if not interaction.guild:
            await interaction.response.send_message("❌ Must be used inside a Discord server.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        try:
            project = await self.project_service.get_by_name(interaction.guild.id, project_name)
            if not project:
                await interaction.followup.send(f"❌ Project '{project_name}' not found.", ephemeral=True)
                return

            await self.auth_service.require_task_creation(interaction.user, project.id)
            if assignee:
                await self.auth_service.require_task_assignee_eligibility(interaction.guild, assignee, project.id)

            raw_prereqs = prereqs or depends_on
            prereq_ids = extract_short_ids(raw_prereqs)
            if raw_prereqs and raw_prereqs.strip() and not prereq_ids:
                await interaction.followup.send(
                    f"❌ Could not parse any valid task short IDs from prereqs: `{raw_prereqs}`.\n"
                    "Expected format: `INF-1` or `INF-1, INF-2`.",
                    ephemeral=True,
                )
                return

            prereq_tasks: list[Task] = []
            for pid in prereq_ids:
                prereq_task = await self.task_service.get_by_short_id(interaction.guild.id, pid)
                if not prereq_task:
                    await interaction.followup.send(
                        f"❌ Prerequisite task `{pid}` was not found in this server.",
                        ephemeral=True,
                    )
                    return
                prereq_tasks.append(prereq_task)

            due_at = parse_natural_date(due) if due else None
            watchers = extract_user_ids(cc)
            p_level = (
                PriorityLevel(priority.lower())
                if priority.lower() in PriorityLevel._value2member_map_
                else PriorityLevel.NORMAL
            )

            create_kwargs: dict[str, Any] = {
                "guild_id": interaction.guild.id,
                "title": title,
                "creator_discord_id": interaction.user.id,
                "project_id": project.id,
                "assignee_discord_id": assignee.id if assignee else None,
                "due_at": due_at,
                "priority": p_level,
                "body": description,
                "watchers": watchers,
            }
            if prereq_ids:
                create_kwargs["prerequisite_short_ids"] = prereq_ids

            task, ref = await self.task_service.create_task_with_workspace(
                guild_id=interaction.guild.id,
                title=title,
                creator_discord_id=interaction.user.id,
                project_id=project.id,
                project_name=project.name,
                assignee_discord_id=assignee.id if assignee else None,
                due_at=due_at,
                priority=p_level,
                body=description,
                watchers=watchers,
                prerequisite_short_ids=prereq_ids,
                target_container=interaction.channel,
                preferred_channel_id=interaction.channel_id,
                workspace=self.workspace,
            )

            prereqs_suffix = ""
            if prereq_ids:
                prereqs_suffix = f"\n🔗 **Prerequisites**: {', '.join(f'`[{p}]`' for p in prereq_ids)}"

            if ref:
                if ref.channel_id != interaction.channel_id:
                    await interaction.followup.send(
                        f"✅ Created task **[{task.short_id}] {task.title}** in <#{ref.channel_id}>"
                        + f" with thread <#{ref.thread_id}>"
                        + prereqs_suffix
                    )
                else:
                    await interaction.followup.send(
                        f"✅ Task **[{task.short_id}]** created in project **{project.name}**." + prereqs_suffix,
                        embed=build_task_embed(task, project_name=project.name, prerequisites=prereq_tasks or None),
                    )
            else:
                await interaction.followup.send(
                    f"✅ Task **[{task.short_id}]** created in project **{project.name}**." + prereqs_suffix,
                    embed=build_task_embed(task, project_name=project.name, prerequisites=prereq_tasks or None),
                )
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"creating task in project '{project_name}'", logger, ephemeral=True
            )

    @task_group.command(name="list", description="Display filtered active tasks with interactive pagination.")
    @app_commands.describe(
        project_name="Filter by project name",
        user="Filter by assignee",
        status="Filter by execution status",
        overdue="Filter for overdue tasks only",
    )
    @app_commands.choices(
        status=[
            app_commands.Choice(name="Active (In Progress & Not Started)", value="active"),
            app_commands.Choice(name="In Progress", value="inProgress"),
            app_commands.Choice(name="Not Started", value="notStarted"),
            app_commands.Choice(name="Completed", value="completed"),
            app_commands.Choice(name="All (including Completed)", value="all"),
        ]
    )
    @app_commands.autocomplete(project_name=project_autocomplete)
    async def task_list(
        self,
        interaction: discord.Interaction,
        project_name: str | None = None,
        user: discord.Member | None = None,
        status: str | None = None,
        overdue: bool = False,
    ) -> None:
        if not interaction.guild:
            return

        await interaction.response.defer(ephemeral=True)
        try:
            project_id = None
            title_parts = []
            if project_name:
                project = await self.project_service.get_by_name(interaction.guild.id, project_name)
                if not project:
                    await interaction.followup.send(f"❌ Project '{project_name}' not found.", ephemeral=True)
                    return
                project_id = project.id
                title_parts.append(f"Project: {project.name}")

            if user:
                title_parts.append(f"Assignee: {user.display_name}")

            if overdue:
                title_parts.append("Status: ⏰ Overdue")

            exclude_completed = False
            filter_status = None
            if status == "all":
                filter_status = None
                exclude_completed = False
                if not overdue:
                    title_parts.append("Status: All")
            elif status in ("inProgress", "notStarted", "completed"):
                filter_status = TaskStatus(status)
                exclude_completed = False
                if not overdue:
                    title_parts.append(f"Status: {filter_status.value}")
            else:
                filter_status = None
                exclude_completed = True
                if status == "active" and not overdue:
                    title_parts.append("Status: Active")

            tasks, total_count = await self.task_service.list_tasks(
                guild_id=interaction.guild.id,
                project_id=project_id,
                assignee_discord_id=user.id if user else None,
                status=filter_status,
                include_archived=False,
                exclude_completed=exclude_completed,
                overdue_only=overdue,
                limit=100,
            )

            title_context = "Tasks (" + ", ".join(title_parts) + ")" if title_parts else "Active Tasks"
            embed = build_page_embed(tasks, 0, total_count, title_context)
            view = TaskListView(tasks, total_count, title_context)

            await interaction.followup.send(embed=embed, view=view)
            from src.adapters.discord_bot.menu_manager import menu_manager

            await menu_manager.register_menu(interaction)
        except Exception as e:
            await send_interaction_error(interaction, e, "listing tasks", logger, ephemeral=True)

    async def _resolve_task_context(
        self,
        interaction: discord.Interaction,
        task_short_id: str | None,
    ) -> Task | None:
        """Resolves target task either from explicitly provided short ID or implicitly from thread context."""
        if not interaction.guild:
            return None

        if task_short_id:
            task_entity = await self.task_service.get_by_short_id(interaction.guild.id, task_short_id)
            if not task_entity:
                await interaction.followup.send(f"❌ Task '{task_short_id}' not found.", ephemeral=True)
                return None
            return task_entity

        # Implicit resolution from thread workspace channel ID
        if interaction.channel_id:
            task_entity = await self.task_service.get_by_thread_id(interaction.guild.id, interaction.channel_id)
            if task_entity:
                return task_entity

        await interaction.followup.send(
            "❌ No task specified and this command was not executed inside an active task thread workspace.\n"
            "Please specify a task short ID (e.g. `APP-1`) or run this command inside the task's thread.",
            ephemeral=True,
        )
        return None

    @task_group.command(name="history", description="View the full audit trail and status history of a task.")
    @app_commands.describe(task="Task identifier (search by short ID or title, or omit if inside thread)")
    @app_commands.autocomplete(task=task_autocomplete)
    async def task_history(self, interaction: discord.Interaction, task: str | None = None) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            task_entity = await self._resolve_task_context(interaction, task)
            if not task_entity:
                return

            history = await self.task_service.get_history(task_entity.id)
            embed = build_task_history_embed(task_entity, history)
            await interaction.followup.send(embed=embed)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"retrieving history for task '{task or 'current thread'}'", logger, ephemeral=True
            )

    @task_group.command(name="assign", description="Assign or reassign a task to a squad member.")
    @app_commands.describe(
        task="Short ID of the task (e.g. APP-1, or omit if inside thread)",
        assignee="Discord member to assign (or omit to unassign)",
    )
    @app_commands.autocomplete(task=task_autocomplete)
    async def task_assign(
        self,
        interaction: discord.Interaction,
        task: str | None = None,
        assignee: discord.Member | None = None,
    ) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            task_entity = await self._resolve_task_context(interaction, task)
            if not task_entity:
                return

            await self.auth_service.require_task_mutation(interaction.user, task_entity)
            if assignee:
                await self.auth_service.require_task_assignee_eligibility(
                    interaction.guild, assignee, task_entity.project_id
                )

            new_assignee_id = assignee.id if assignee else None
            updated_task = await self.task_service.update_assignee(
                task_id=task_entity.id,
                new_assignee_id=new_assignee_id,
                actor_discord_id=interaction.user.id,
            )

            msg = (
                f"👤 Assigned **[{updated_task.short_id}]** to <@{new_assignee_id}>."
                if new_assignee_id
                else f"👤 Unassigned **[{updated_task.short_id}]**."
            )
            embed = build_task_embed(updated_task)
            if self.workspace:
                await self.workspace.sync_workspace(updated_task)
            elif hasattr(self.bot, "sync_root_task_message"):
                res = self.bot.sync_root_task_message(updated_task)
                if hasattr(res, "__await__"):
                    await res

            await interaction.followup.send(msg, embed=embed)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"assigning task '{task or 'current thread'}'", logger, ephemeral=True
            )

    @task_group.command(name="status", description="Update the execution status of a task.")
    @app_commands.describe(
        status="New task status",
        task="Short ID of the task (omit if inside thread)",
        notes="Optional transition notes",
        force="Bypass dependency guard if task has incomplete blockers",
    )
    @app_commands.choices(
        status=[
            app_commands.Choice(name="⏳ Not Started", value="not_started"),
            app_commands.Choice(name="🟡 In Progress", value="in_progress"),
            app_commands.Choice(name="✅ Completed", value="completed"),
        ]
    )
    @app_commands.autocomplete(task=task_autocomplete)
    async def task_status(
        self,
        interaction: discord.Interaction,
        status: str,
        task: str | None = None,
        notes: str | None = None,
        force: bool = False,
    ) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            task_entity = await self._resolve_task_context(interaction, task)
            if not task_entity:
                return

            await self.auth_service.require_task_mutation(interaction.user, task_entity)
            status_map = {
                "not_started": TaskStatus.NOT_STARTED,
                "notstarted": TaskStatus.NOT_STARTED,
                "in_progress": TaskStatus.IN_PROGRESS,
                "inprogress": TaskStatus.IN_PROGRESS,
                "completed": TaskStatus.COMPLETED,
            }
            normalized_status = status.lower().replace("-", "_")
            new_status = status_map.get(normalized_status)
            if not new_status:
                try:
                    new_status = TaskStatus(status)
                except ValueError:
                    await interaction.followup.send(f"❌ Invalid status: '{status}'.", ephemeral=True)
                    return

            if new_status in (TaskStatus.IN_PROGRESS, TaskStatus.COMPLETED):
                unresolved = await self.task_service.get_unresolved_prerequisites(task_entity.id)
                if unresolved:
                    if not force:
                        embed, view = DiscordViewRouter.build_task_blocked_confirm_view(
                            task=task_entity,
                            target_status=new_status,
                            incomplete_prereqs=unresolved,
                            author_id=interaction.user.id,
                            task_service=self.task_service,
                            auth_service=self.auth_service,
                            workspace=self.workspace,
                            bot=self.bot,
                            notes=notes,
                        )
                        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
                        return
                    else:
                        if self.auth_service:
                            await self.auth_service.require_dependency_bypass(interaction.user, task_entity)
                        blocker_ids = ", ".join(f"[{p.short_id}]" for p in unresolved)
                        bypass_note = f"bypassed unresolved blockers: {blocker_ids}"
                        notes = (
                            f"{notes} ({bypass_note})"
                            if notes
                            else f"Status updated to {new_status.value} ({bypass_note})"
                        )

            updated_task = await self.task_service.update_status(
                task_id=task_entity.id,
                new_status=new_status,
                expected_version=task_entity.version,
                actor_discord_id=interaction.user.id,
                notes=notes,
            )

            msg = f"🔄 Status updated to **{new_status.value.upper()}** for **[{updated_task.short_id}]**."
            embed = build_task_embed(updated_task)
            if self.workspace:
                await self.workspace.sync_workspace(updated_task)
            elif hasattr(self.bot, "sync_root_task_message"):
                res = self.bot.sync_root_task_message(updated_task)
                if hasattr(res, "__await__"):
                    await res

            await interaction.followup.send(msg, embed=embed)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"updating status for task '{task or 'current thread'}'", logger, ephemeral=True
            )

    @task_group.command(name="update", description="Update a task's title, description, or due date.")
    @app_commands.describe(
        task="Short ID of the task to update (omit if inside thread)",
        title="New task title",
        description="New task description (use 'clear' to remove)",
        due="New due date (e.g. 'tomorrow', 'in 3 days', 'clear')",
    )
    @app_commands.autocomplete(task=task_autocomplete)
    async def task_update(
        self,
        interaction: discord.Interaction,
        task: str | None = None,
        title: str | None = None,
        description: str | None = None,
        due: str | None = None,
    ) -> None:
        if not interaction.guild:
            return
        await interaction.response.defer(ephemeral=True)
        try:
            task_entity = await self._resolve_task_context(interaction, task)
            if not task_entity:
                return

            if self.auth_service:
                await self.auth_service.require_task_mutation(interaction.user, task_entity)

            update_kwargs: dict[str, Any] = {}
            if title is not None:
                cleaned_title = title.strip()
                if not cleaned_title:
                    await interaction.followup.send("❌ Task title cannot be empty.", ephemeral=True)
                    return
                update_kwargs["title"] = cleaned_title

            if description is not None:
                desc_val = description.strip()
                clear_body = desc_val == "" or desc_val.lower() in ("clear", "none", "remove")
                update_kwargs["clear_body"] = clear_body
                update_kwargs["body"] = None if clear_body else desc_val

            if due is not None:
                due_val = due.strip()
                clear_due = due_val.lower() in ("clear", "none", "remove", "null", "unset")
                update_kwargs["clear_due_at"] = clear_due
                update_kwargs["due_at"] = None if clear_due else parse_natural_date(due_val)

            if not update_kwargs:
                await interaction.followup.send("ℹ️ No updates specified.", ephemeral=True)
                return

            updated_task = await self.task_service.update_details(
                task_id=task_entity.id,
                actor_discord_id=interaction.user.id,
                **update_kwargs,
            )

            sync_res = None
            if self.workspace:
                sync_res = await self.workspace.sync_workspace(
                    updated_task,
                    sync_title=True,
                    sync_starter_card=True,
                )
            elif hasattr(self.bot, "sync_task_thread"):
                sync_res = await self.bot.sync_task_thread(updated_task, sync_title=True)
            elif hasattr(self.bot, "sync_root_task_message"):
                res = self.bot.sync_root_task_message(updated_task)
                if hasattr(res, "__await__"):
                    await res

            title_deferred = hasattr(sync_res, "title_deferred") and sync_res.title_deferred
            msg = f"✏️ Updated details for **[{updated_task.short_id}]**."
            if title_deferred:
                msg += (
                    "\nℹ️ Task title updated. (Note: Discord limits thread renames to 2 per 10 minutes; "
                    "thread title will reflect changes shortly)."
                )

            embed = build_task_embed(updated_task)
            await interaction.followup.send(msg, embed=embed)
            from src.adapters.discord_bot.menu_manager import menu_manager

            menu_manager.schedule_toast_dismissal(interaction, delay=8.0)
        except Exception as e:
            await send_interaction_error(
                interaction, e, f"updating details for task '{task or 'current thread'}'", logger, ephemeral=True
            )
