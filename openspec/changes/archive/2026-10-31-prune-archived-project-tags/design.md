# Design: Prune Archived Project Tags

## Context

See `proposal.md` for motivation and background. Discord forum channels enforce a maximum of 20 `available_tags`. DiscordProjectManager allocates up to 8 standard workflow/status tags, leaving 12 slots for per-project tags (`📁 <Project Name>`). Because forum channels are shared across multiple projects, failing to remove tags when projects are archived causes tag exhaustion over time.

## Goals / Non-Goals

**Goals:**
- Provide a robust helper `remove_project_tag(forum_channel, project_name)` to remove an archived project's tag from Discord `available_tags`.
- Wire `remove_project_tag` into the project archival workflow in `ProjectArchiveConfirmView`.
- In `ensure_pinned_hub_post`, identify any tags belonging to known archived projects for the channel and prune them during hub reconciliation.
- Gracefully handle tag capacity limits (20 tags) upon project restoration without aborting the database unarchive operation.

**Non-Goals:**
- Modifying thread starter embeds or thread titles (thread title prefix `[PREFIX-X]` already provides persistent task identity).
- Introducing a shared/generic `📁 Archived` tag (as confirmed during exploration, filtering archived tasks in the Discord forum sidebar is not required).
- Rebuilding or restructuring forum channels automatically.

## Decisions

### Decision 1: Immediate tag removal upon archive vs. lazy eviction
- **Rationale**: Immediate tag removal is deterministic, simple, and ensures the forum sidebar always shows only active projects. Discord automatically strips the deleted tag from threads without requiring thread edits.
- **Alternatives Considered**:
  - *Lazy eviction on cap*: Keeping tags until 20 is reached adds state complexity and results in unpredictable tag deletion during unrelated project creation.
  - *Shared `📁 Archived` tag*: Adding a consolidated tag consumes an extra tag slot without adding significant value since task titles already retain the project prefix.

### Decision 2: Location and signature of tag pruning helpers
- **Decision**: Implement `remove_project_tag(forum_channel: discord.ForumChannel, project_name: str) -> tuple[bool, str | None]` in `src/adapters/discord_bot/views/forum_helpers.py`, alongside existing `ensure_project_tag` and `setup_forum_tags`.
- **Rationale**: Keeps Discord tag operations consolidated in the forum adapter layer without leaking discord API primitives into domain services or ports.

### Decision 3: Reconciliation during pinned hub refresh
- **Decision**: In `ensure_pinned_hub_post`, retrieve archived projects for the guild (`project_service.list_projects(guild.id, include_archived=True)`), filter for those bound to `channel.id` that are archived (`p.is_archived`), and remove any matching tags from `available_tags`.
- **Rationale**: This cleans up historical tags previously leaked before this feature existed, whenever a hub post is refreshed or checked.

## Risks / Trade-offs

- **[Risk] Existing archived threads lose their project filter badge in Discord** → **Mitigation**: Project prefix `[PREFIX-1]` remains in thread names, and starter cards retain the project name and metadata. User confirmed sidebar filtering for archived projects is not needed.
- **[Risk] Bot lacks `Manage Channels` permission when archiving** → **Mitigation**: Wrap tag removal in try-except block catching `discord.Forbidden` and generic exceptions, logging warnings rather than aborting database archival.
- **[Risk] Restoring an archived project when the forum already has 20 active tags** → **Mitigation**: `ensure_project_tag` returns an error message when `len(available_tags) >= 20`. `ProjectRestoreConfirmView` displays this as an informational warning without failing the restore.

## Migration Plan

- Non-breaking change.
- Existing forum channels with orphaned tags from historical archived projects will automatically be cleaned up on the next `ensure_pinned_hub_post` run or project archive event.
