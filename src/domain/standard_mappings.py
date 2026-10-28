from datetime import UTC, datetime, timedelta
from typing import Any

from src.domain.models import Task


def to_rfc5545_vtodo(task: Task) -> str:
    """Serializes a domain Task into an RFC 5545 VTODO component string."""
    now_utc = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VTODO",
        f"UID:{task.id}",
        f"DTSTAMP:{now_utc}",
        f"SUMMARY:{task.title}",
        f"STATUS:{task.status.rfc5545_status}",
        f"PRIORITY:{task.priority.rfc5545_priority}",
        f"ORGANIZER;CN=Discord:mailto:{task.creator_discord_id}@discord.local",
    ]

    if task.body:
        # Escape newlines for RFC 5545
        escaped_body = task.body.replace("\n", "\\n")
        lines.append(f"DESCRIPTION:{escaped_body}")

    if task.due_at:
        due_str = task.due_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        lines.append(f"DUE:{due_str}")

    if task.completed_at:
        completed_str = task.completed_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
        lines.append(f"COMPLETED:{completed_str}")

    if task.assignee_discord_id:
        lines.append(f"ATTENDEE;ROLE=REQ-PARTICIPANT;CN=Assignee:mailto:{task.assignee_discord_id}@discord.local")

    for watcher_id in task.watchers:
        lines.append(f"ATTENDEE;ROLE=NON-PARTICIPANT;CN=Watcher:mailto:{watcher_id}@discord.local")

    # Custom extensions for Discord metadata
    lines.append(f"X-DISCORD-GUILD-ID:{task.guild_id}")
    lines.append(f"X-DISCORD-SHORT-ID:{task.short_id}")
    if task.project_id:
        lines.append(f"X-DISCORD-PROJECT-ID:{task.project_id}")

    lines.append("END:VTODO")
    return "\r\n".join(lines)


def to_rfc5545_vevent(task: Task) -> str | None:
    """Serializes a domain Task with a due date into an RFC 5545 VEVENT component string.

    Returns None if the task has no due_at timestamp.
    """
    if not task.due_at:
        return None

    now_utc = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    due_dt = task.due_at if task.due_at.tzinfo else task.due_at.replace(tzinfo=UTC)
    due_utc = due_dt.astimezone(UTC)
    start_utc = due_utc - timedelta(hours=1)
    dtstart_str = start_utc.strftime("%Y%m%dT%H%M%SZ")
    dtend_str = due_utc.strftime("%Y%m%dT%H%M%SZ")

    # In VEVENT, valid STATUS values are TENTATIVE, CONFIRMED, CANCELLED.
    if task.is_archived:
        status_str = "CANCELLED"
    else:
        status_str = "CONFIRMED"

    summary = f"[{task.short_id}] {task.title}"

    lines = [
        "BEGIN:VEVENT",
        f"UID:{task.id}",
        f"DTSTAMP:{now_utc}",
        f"SUMMARY:{summary}",
        f"STATUS:{status_str}",
        f"PRIORITY:{task.priority.rfc5545_priority}",
        f"DTSTART:{dtstart_str}",
        f"DTEND:{dtend_str}",
        f"ORGANIZER;CN=Discord:mailto:{task.creator_discord_id}@discord.local",
    ]

    if task.body:
        escaped_body = task.body.replace("\r\n", "\\n").replace("\n", "\\n")
        lines.append(f"DESCRIPTION:{escaped_body}")

    if task.assignee_discord_id:
        lines.append(f"ATTENDEE;ROLE=REQ-PARTICIPANT;CN=Assignee:mailto:{task.assignee_discord_id}@discord.local")

    for watcher_id in task.watchers:
        lines.append(f"ATTENDEE;ROLE=NON-PARTICIPANT;CN=Watcher:mailto:{watcher_id}@discord.local")

    # Custom extensions for Discord metadata
    lines.append(f"X-DISCORD-GUILD-ID:{task.guild_id}")
    lines.append(f"X-DISCORD-SHORT-ID:{task.short_id}")
    if task.project_id:
        lines.append(f"X-DISCORD-PROJECT-ID:{task.project_id}")
    lines.append(f"X-DISCORD-STATUS:{task.status.value}")

    lines.append("END:VEVENT")
    return "\r\n".join(lines)


def to_rfc5545_vcalendar(
    tasks: list[Task],
    cal_name: str = "DGG-PM Tasks",
    format_type: str = "event",
) -> str:
    """Wraps domain Tasks into a valid RFC 5545 VCALENDAR envelope.

    Args:
        tasks: List of domain tasks to serialize.
        cal_name: Calendar name for X-WR-CALNAME.
        format_type: 'event' (default, VEVENT components for Google/Apple/Outlook),
                     'todo' (VTODO components for Reminders/OmniFocus),
                     'both' (both VEVENT and VTODO components).
    """
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Digital Ground Game//Discord Project Manager//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{cal_name}",
        "X-WR-TIMEZONE:UTC",
    ]

    for task in tasks:
        if format_type in ("event", "both"):
            vevent = to_rfc5545_vevent(task)
            if vevent:
                lines.append(vevent)
        if format_type in ("todo", "both"):
            vtodo = to_rfc5545_vtodo(task)
            lines.append(vtodo)

    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def to_ms_graph_todo_task(task: Task) -> dict[str, Any]:
    """Serializes a domain Task into Microsoft Graph todoTask JSON schema."""
    graph_data: dict[str, Any] = {
        "id": str(task.id),
        "title": task.title,
        "status": task.status.value,
        "importance": task.priority.value,
        "isReminderOn": bool(task.due_at),
        "createdDateTime": task.created_at.astimezone(UTC).isoformat(),
        "lastModifiedDateTime": task.updated_at.astimezone(UTC).isoformat(),
        "body": {
            "content": task.body or "",
            "contentType": "text",
        },
        "extensions": [
            {
                "id": "discordMetadata",
                "guildId": str(task.guild_id),
                "shortId": task.short_id,
                "projectId": str(task.project_id) if task.project_id else None,
                "creatorDiscordId": str(task.creator_discord_id),
                "assigneeDiscordId": str(task.assignee_discord_id) if task.assignee_discord_id else None,
                "watchers": [str(w) for w in task.watchers],
            }
        ],
    }

    if task.due_at:
        graph_data["dueDateTime"] = {
            "dateTime": task.due_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
            "timeZone": "UTC",
        }

    if task.completed_at:
        graph_data["completedDateTime"] = {
            "dateTime": task.completed_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S"),
            "timeZone": "UTC",
        }

    return graph_data
