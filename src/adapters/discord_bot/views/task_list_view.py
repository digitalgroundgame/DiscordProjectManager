import math
from datetime import UTC

import discord

from src.adapters.discord_bot.views.base_view import BaseView
from src.domain.enums import TaskStatus
from src.domain.models import Task

PAGE_SIZE = 5

STATUS_EMOJIS = {
    TaskStatus.NOT_STARTED: "⚪",
    TaskStatus.IN_PROGRESS: "🟡",
    TaskStatus.COMPLETED: "🟢",
}


def build_page_embed(
    tasks: list[Task],
    page: int,
    total_count: int,
    title_context: str = "Active Tasks",
) -> discord.Embed:
    total_pages = max(1, math.ceil(total_count / PAGE_SIZE))
    start_idx = page * PAGE_SIZE
    page_tasks = tasks[start_idx : start_idx + PAGE_SIZE]

    embed = discord.Embed(
        title=f"📋 {title_context} (Total: {total_count})",
        color=discord.Color.blurple(),
    )

    if not page_tasks:
        embed.description = "*No tasks matching the specified criteria.*"
        embed.set_footer(text=f"Page {page + 1} of {total_pages}")
        return embed

    from src.adapters.discord_bot.views.task_embed import get_task_jump_url

    lines = []
    for t in page_tasks:
        emoji = STATUS_EMOJIS.get(t.status, "⚪")
        assignee_str = f"<@{t.assignee_discord_id}>" if t.assignee_discord_id else "Unassigned"

        due_str = ""
        if t.due_at:
            due_ts = int(t.due_at.astimezone(UTC).timestamp())
            due_str = f" • Due <t:{due_ts}:R>"

        jump_url = get_task_jump_url(t)
        if jump_url:
            title_part = f"**[[{t.short_id}] {t.title}]({jump_url})**"
        else:
            title_part = f"**[{t.short_id}]** {t.title}"

        lines.append(f"{emoji} {title_part}\n   ↳ Assignee: {assignee_str}{due_str}")

    embed.description = "\n\n".join(lines)
    embed.set_footer(text=f"Page {page + 1} of {total_pages}")
    return embed


class TaskListView(BaseView):
    """Interactive paginated view for /task-list."""

    def __init__(
        self,
        tasks: list[Task],
        total_count: int,
        title_context: str = "Active Tasks",
        current_page: int = 0,
        initial_interaction: discord.Interaction | None = None,
    ):
        super().__init__(timeout=180)
        self.tasks = tasks
        self.total_count = total_count
        self.title_context = title_context
        self.current_page = current_page
        self.total_pages = max(1, math.ceil(total_count / PAGE_SIZE))
        self._initial_interaction = initial_interaction
        self._update_buttons()

    async def on_timeout(self) -> None:
        try:
            if (
                hasattr(self, "_initial_interaction")
                and self._initial_interaction
                and hasattr(self._initial_interaction, "delete_original_response")
            ):
                await self._initial_interaction.delete_original_response()
        except Exception:
            pass

    def _update_buttons(self) -> None:
        self.prev_btn.disabled = self.current_page <= 0
        self.next_btn.disabled = self.current_page >= self.total_pages - 1

    @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.secondary, custom_id="task_list:prev")
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.current_page > 0:
            self.current_page -= 1
            self._update_buttons()
            embed = build_page_embed(self.tasks, self.current_page, self.total_count, self.title_context)
            await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary, custom_id="task_list:next")
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        if self.current_page < self.total_pages - 1:
            self.current_page += 1
            self._update_buttons()
            embed = build_page_embed(self.tasks, self.current_page, self.total_count, self.title_context)
            await interaction.response.edit_message(embed=embed, view=self)
