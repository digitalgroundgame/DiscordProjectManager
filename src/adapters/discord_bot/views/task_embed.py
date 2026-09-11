from datetime import UTC

import discord

from src.domain.enums import PriorityLevel, TaskStatus
from src.domain.models import Task, TaskHistory

STATUS_COLORS = {
    TaskStatus.NOT_STARTED: discord.Color.blue(),
    TaskStatus.IN_PROGRESS: discord.Color.gold(),
    TaskStatus.COMPLETED: discord.Color.brand_green(),
}

STATUS_EMOJIS = {
    TaskStatus.NOT_STARTED: "⚪ Not Started",
    TaskStatus.IN_PROGRESS: "🟡 In Progress",
    TaskStatus.COMPLETED: "🟢 Completed",
}

PRIORITY_LABELS = {
    PriorityLevel.HIGH: "High",
    PriorityLevel.NORMAL: "Normal",
    PriorityLevel.LOW: "Low",
}


def resolve_task_jump_url(
    guild_id: int | None,
    thread_id: int | None,
    message_id: int | None = None,
) -> str | None:
    """Generates direct Discord message or thread jump URL given IDs."""
    if not guild_id or not thread_id:
        return None
    if message_id:
        return f"https://discord.com/channels/{guild_id}/{thread_id}/{message_id}"
    return f"https://discord.com/channels/{guild_id}/{thread_id}"


def get_task_jump_url(task: Task) -> str | None:
    """Generates direct Discord message or thread jump URL for a task."""
    return resolve_task_jump_url(task.guild_id, task.discord_thread_id, task.discord_message_id)


def build_task_embed(
    task: Task,
    project_name: str | None = None,
    prerequisites: list[Task] | None = None,
    dependents: list[Task] | None = None,
) -> discord.Embed:
    """Builds an Expanded Visual Card Discord Embed representing a task."""
    color = discord.Color.dark_grey() if task.is_archived else STATUS_COLORS.get(task.status, discord.Color.blue())

    prefix_title = f"[{task.short_id}] {task.title}"
    if task.is_archived:
        prefix_title = f"[ARCHIVED] {prefix_title}"

    jump_url = get_task_jump_url(task)
    desc_text = task.body or "*No additional description provided.*"

    embed = discord.Embed(
        title=prefix_title,
        url=jump_url,
        description=f"### Overview\n{desc_text}",
        color=color,
        timestamp=task.created_at,
    )

    # 1. Status & Priority Section
    status_label = STATUS_EMOJIS.get(task.status, task.status.value)
    priority_label = PRIORITY_LABELS.get(task.priority, task.priority.value)
    embed.add_field(
        name="Status & Priority",
        value=f"• **State**: {status_label}\n• **Priority**: {priority_label}",
        inline=True,
    )

    # 2. Team & Contributor Section
    proj_display = project_name or (str(task.project_id) if task.project_id else "Standalone")
    assignee_val = f"<@{task.assignee_discord_id}>" if task.assignee_discord_id else "*Unassigned*"
    embed.add_field(
        name="Team & Assignee",
        value=(
            f"• **Project**: **{proj_display}**\n"
            f"• **Assignee**: {assignee_val}\n"
            f"• **Created By**: <@{task.creator_discord_id}>"
        ),
        inline=True,
    )

    # 3. Dependencies & Prerequisites Section
    if prerequisites or dependents:
        dep_lines = []
        if prerequisites:
            all_done = all(p.status == TaskStatus.COMPLETED for p in prerequisites)
            dep_status = "*Ready*" if all_done else "*Blocked by prerequisites*"
            prereqs_str = ", ".join(f"`[{p.short_id}]`" for p in prerequisites)
            dep_lines.append(f"• **Prerequisites**: {prereqs_str} ({dep_status})")
        if dependents:
            deps_str = ", ".join(f"`[{d.short_id}]`" for d in dependents)
            dep_lines.append(f"• **Unlocks**: {deps_str}")
        embed.add_field(
            name="Dependencies",
            value="\n".join(dep_lines),
            inline=False,
        )

    # 4. Timeline & Dates Section
    time_lines = []
    created_ts = int(task.created_at.astimezone(UTC).timestamp()) if task.created_at else 0
    time_lines.append(f"• **Created**: <t:{created_ts}:f> (<t:{created_ts}:R>)")
    if task.due_at:
        due_ts = int(task.due_at.astimezone(UTC).timestamp())
        time_lines.append(f"• **Target Due Date**: <t:{due_ts}:f> (<t:{due_ts}:R>)")
    else:
        time_lines.append("• **Target Due Date**: *No deadline set*")

    if task.watchers:
        watchers_str = ", ".join(f"<@{uid}>" for uid in task.watchers)
        time_lines.append(f"• **Watchers**: {watchers_str}")

    embed.add_field(
        name="Timeline & Details",
        value="\n".join(time_lines),
        inline=False,
    )

    embed.set_footer(text=f"dgg-pm • v{task.version} • ID: {task.short_id}")
    return embed


def build_thread_workspace_content(task: Task) -> str:
    """Builds the message content for the task workspace inside a discussion thread."""
    description = task.body.strip() if task.body and task.body.strip() else "*No additional description provided.*"
    if len(description) > 1500:
        description = description[:1497] + "..."

    assignee_str = f"<@{task.assignee_discord_id}>" if task.assignee_discord_id else "*Unassigned*"
    priority_str = f"`{PRIORITY_LABELS.get(task.priority, task.priority.value.title())}`"

    meta_parts = [
        f"**Assignee**: {assignee_str}",
        f"**Priority**: {priority_str}",
    ]
    if task.watchers:
        watchers_str = " ".join(f"<@{uid}>" for uid in task.watchers)
        meta_parts.append(f"**Watchers**: {watchers_str}")

    meta_line = " • ".join(meta_parts)
    return f"{description}\n\n{meta_line}"


def build_task_history_embed(task: Task, history: list[TaskHistory]) -> discord.Embed:
    """Builds an embed listing the chronological audit history of a task."""
    jump_url = get_task_jump_url(task)
    embed = discord.Embed(
        title=f"Audit Trail • [{task.short_id}] {task.title}",
        url=jump_url,
        color=discord.Color.dark_grey(),
    )
    if not history:
        embed.description = "*No history recorded for this task.*"
        return embed

    lines = [
        "### Activity History",
    ]
    for h in history:
        ts = int(h.created_at.astimezone(UTC).timestamp())
        actor = f"<@{h.actor_discord_id}>"
        time_str = f"<t:{ts}:R>"

        detail = ""
        if h.old_status and h.new_status:
            detail = f" transitioned status: `{h.old_status.value}` ➔ `{h.new_status.value}`"
        elif h.action:
            detail = f" performed action: `{h.action.value}`"

        if h.notes:
            detail += f'\n  *"{h.notes}"*'

        lines.append(f"• **{time_str}** by {actor}:{detail}")

    embed.description = "\n".join(lines)
    embed.set_footer(text=f"dgg-pm • Audit Trail • Task {task.short_id}")
    return embed
