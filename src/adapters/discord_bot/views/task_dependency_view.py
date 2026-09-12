from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from src.adapters.discord_bot.views.base_view import BaseView
from src.domain.enums import TaskStatus
from src.domain.exceptions import ValidationError
from src.domain.models import Task

if TYPE_CHECKING:
    from src.services.task_service import TaskService

logger = logging.getLogger("dgg_pm.views.task_dependency")


def build_dependency_embed(task: Task, prerequisites: list[Task], dependents: list[Task]) -> discord.Embed:
    desc = (
        f"**Task:** {task.title}\n"
        f"Configure prerequisite tasks that must be completed before **[{task.short_id}]** can be started."
    )
    embed = discord.Embed(
        title=f"Manage Dependencies: [{task.short_id}]",
        description=desc,
        color=discord.Color.from_rgb(16, 152, 247),
    )

    if prerequisites:
        lines = []
        for p in prerequisites:
            status_icon = "🟢" if p.is_completed else ("🟡" if p.status == TaskStatus.IN_PROGRESS else "⚪")
            lines.append(f"{status_icon} **`[{p.short_id}]`** {p.title}")
        embed.add_field(name=f"Prerequisites ({len(prerequisites)})", value="\n".join(lines), inline=False)
    else:
        embed.add_field(
            name="Prerequisites (0)",
            value="*No prerequisites — this task can be started immediately.*",
            inline=False,
        )

    if dependents:
        lines = []
        for d in dependents:
            status_icon = "🟢" if d.is_completed else ("🟡" if d.status == TaskStatus.IN_PROGRESS else "⚪")
            lines.append(f"{status_icon} **`[{d.short_id}]`** {d.title}")
        embed.add_field(
            name=f"Unlocks ({len(dependents)})",
            value="\n".join(lines) + "\n*(These tasks require this task to finish first)*",
            inline=False,
        )

    embed.set_footer(text="Use the multi-select dropdown below to add or remove prerequisites.")
    return embed


class TaskDependencyView(BaseView):
    """Interactive view allowing members to assign or unassign prerequisite tasks."""

    def __init__(
        self,
        task_service: TaskService,
        task: Task,
        sibling_tasks: list[Task],
        prerequisites: list[Task],
        dependents: list[Task],
    ):
        super().__init__(timeout=180)
        self.task_service = task_service
        self.task = task
        self.sibling_tasks = sibling_tasks
        self.prerequisites = prerequisites
        self.dependents = dependents

        self._build_components()

    def _build_components(self) -> None:
        self.clear_items()

        # Build Select Options for Sibling Tasks (excluding self)
        candidate_tasks = [t for t in self.sibling_tasks if t.id != self.task.id]
        prereq_ids = {p.id for p in self.prerequisites}

        if candidate_tasks:
            options: list[discord.SelectOption] = []
            for t in candidate_tasks[:25]:
                emoji = "🟢" if t.is_completed else ("🟡" if t.status == TaskStatus.IN_PROGRESS else "⚪")

                is_selected = t.id in prereq_ids
                desc = (t.title[:85] + "...") if len(t.title) > 85 else t.title
                options.append(
                    discord.SelectOption(
                        label=f"[{t.short_id}]"[:100],
                        value=t.short_id,
                        description=desc,
                        emoji=emoji,
                        default=is_selected,
                    )
                )

            select = discord.ui.Select(
                placeholder="Select prerequisite tasks (blocks this task)...",
                min_values=0,
                max_values=len(options),
                options=options,
                row=0,
            )
            select.callback = self._on_select_prerequisites
            self.add_item(select)

        # Done button
        done_btn = discord.ui.Button(
            label="Done",
            style=discord.ButtonStyle.success,
            row=1,
        )
        done_btn.callback = self._on_done
        self.add_item(done_btn)

    async def _on_select_prerequisites(self, interaction: discord.Interaction) -> None:
        if not interaction.guild:
            return

        selected_short_ids = set(interaction.data.get("values", []))  # type: ignore
        current_prereq_short_ids = {p.short_id for p in self.prerequisites}

        to_add = selected_short_ids - current_prereq_short_ids
        to_remove = current_prereq_short_ids - selected_short_ids

        errors: list[str] = []

        # Remove unselected
        for sid in to_remove:
            try:
                await self.task_service.remove_dependency(
                    guild_id=interaction.guild.id,
                    task_short_id=self.task.short_id,
                    depends_on_short_id=sid,
                    actor_discord_id=interaction.user.id,
                )
            except Exception as e:
                logger.warning("Failed to remove dependency: %s", e)

        # Add newly selected
        for sid in to_add:
            try:
                await self.task_service.add_dependency(
                    guild_id=interaction.guild.id,
                    task_short_id=self.task.short_id,
                    depends_on_short_id=sid,
                    actor_discord_id=interaction.user.id,
                )
            except ValidationError as ve:
                errors.append(f"⚠️ **[{sid}]**: {ve}")
            except Exception as e:
                logger.warning("Failed to add dependency: %s", e)
                errors.append(f"⚠️ **[{sid}]**: Could not link dependency.")

        # Refresh state
        self.prerequisites, self.dependents = await self.task_service.get_task_dependencies(self.task.id)
        self._build_components()

        embed = build_dependency_embed(self.task, self.prerequisites, self.dependents)
        if errors:
            embed.description = "\n".join(errors) + "\n\n" + (embed.description or "")

        await interaction.response.edit_message(embed=embed, view=self)

    async def _on_done(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.edit_message(
            content=f"✅ Dependency settings saved for **[{self.task.short_id}]**.",
            embed=None,
            view=None,
        )
