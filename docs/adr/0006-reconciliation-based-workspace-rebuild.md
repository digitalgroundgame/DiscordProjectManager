# Reconciliation-Based Project Workspace Rebuild

## Context and Decision
When a Discord Forum Channel or project threads are deleted or lost in Discord, the PostgreSQL database remains the canonical source of truth for projects, squad role mappings, tasks, priorities, dependencies, and audit histories. However, Discord snowflake references (`discord_channel_id`, `discord_thread_id`, `discord_message_id`) become stale or broken, preventing members from viewing or updating tasks in Discord.

We decided to introduce a declarative reconciliation-based administrative command (`/pm project rebuild project:<name> [forum:<channel>]`) and interactive dashboard action that reconstructs the complete Discord Project Workspace from database state:
1. **Forum Channel Recovery**: If an optional `forum` channel is provided, the project is rebound to it. If omitted and the database-registered channel no longer exists on Discord, the bot auto-provisions a new `discord.ForumChannel` under the project's saved category (or the default managed category) and configures standard PM tags and Squad role permission overwrites.
2. **Pinned Control Hub Refresh**: The interactive Pinned Control Hub is re-mounted in the forum channel with fresh project buttons and routing.
3. **Task Thread Reconciliation & Archive Invariant**: All tasks in the project are iterated in order. Active tasks with missing threads are re-provisioned with new Thread Workspaces and Task Action Cards. Completed and archived tasks are re-provisioned and immediately archived/locked in Discord to maintain complete, searchable project history while preventing sidebar channel clutter. PostgreSQL is updated atomically with the newly minted Discord snowflake IDs.
4. **Rate-Limit Throttling & Ephemeral Progress**: Because bulk thread creation triggers Discord API rate limits, execution is throttled (0.4s–0.5s pause between thread creations) and reports live progress back to the administrator via an ephemeral progress embed preceded by a confirmation step.

## Considered Options
- **Active-Only Thread Re-creation (Rejected)**: Only recreating open tasks (`NOT_STARTED`, `IN_PROGRESS`) avoids creating threads for historical tasks, but destroys task history, prevents members from searching past work in Discord, and permanently severs completed tasks from their database records.
- **Strict Manual Channel Assignment (Rejected)**: Refusing to create Discord channels automatically forces administrators to manually create and configure forum channels before running the tool, adding unnecessary friction during disaster recovery.
- **Reconciliation with Auto-Provisioning & Archive Invariant (Chosen)**: Restores the complete Discord presence from the database with zero manual channel setup, respects Discord rate limits with live progress reporting, and preserves both full task history and channel cleanliness.

## Consequences
- Administrators can recover from accidental channel/thread deletions in a single command with full data fidelity.
- Historical tasks remain accessible in Discord forum search without polluting active workspace views.
- The PostgreSQL database remains the unambiguous authority for all project state and dependency trees.
