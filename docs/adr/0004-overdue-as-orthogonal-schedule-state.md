# 0004: Overdue as an Orthogonal Temporal Condition

Overdue status is modeled as an orthogonal temporal condition (`task.is_overdue`) evaluated dynamically at query and display time, rather than a 4th mutating lifecycle value in `TaskStatus`. This preserves the underlying work-execution state (`NOT_STARTED` vs. `IN_PROGRESS`), prevents state rollback ambiguities when deadlines are rescheduled, and relies on the transactional Outbox's existing deadline alert (`TASK_DUE_REMINDER`) to apply the `⏰ Overdue` Discord forum tag and post thread alerts at deadline time without requiring continuous background polling writes.

## Considered Options

- **Option 1: 4th Member in `TaskStatus` Enum (`TaskStatus.OVERDUE`)**:
  - *Rejected*: Mutating a task's status from `IN_PROGRESS` to `OVERDUE` destroys the knowledge that an assignee had already begun execution. When an assignee postpones or extends the deadline, the bot cannot determine whether to roll back to `IN_PROGRESS` or `NOT_STARTED` without parsing audit history logs. Furthermore, it conflicts with standard RFC 5545 calendar status mapping (`NEEDS-ACTION`, `IN-PROCESS`, `COMPLETED`).
- **Option 2: Persistent Sub-State Column (`schedule_health: ON_TRACK | OVERDUE`)**:
  - *Rejected*: Storing a persisted boolean or enum column requires continuous database background workers to scan and mutate database records every minute as deadlines pass.
- **Option 3: Orthogonal Temporal Condition (Selected)**:
  - *Accepted*: Evaluated on-demand (`due_at < now and status != COMPLETED`). Visual cards, task lists, and filters treat overdue tasks with red warning badges and dedicated filters, while the Outbox worker dispatches the deadline reminder event, applies the additive `⏰ Overdue` Discord forum tag, and posts a notification.

## Consequences

- Incomplete tasks whose deadlines pass are immediately visible as overdue in UI embeds, task lists, and filters without write-amplification in Postgres.
- The `TaskActionView` retains standard lifecycle controls (`In Progress`, `Complete`) while providing an immediate "Reschedule / Extend" action.
- Completing or archiving a task automatically clears the `⏰ Overdue` tag; reopening a past-due task automatically restores it.
