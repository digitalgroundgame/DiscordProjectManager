# Proposal

## Why

Discord enforces a strict limit of 20 available tags per `ForumChannel`. When multiple projects share a single forum channel, the bot provisions 8 standard project management tags (`Not Started`, `In Progress`, `Completed`, `High Priority`, `Normal Priority`, `Low Priority`, `Unassigned`, `Overdue`) plus a dedicated project tag (`📁 <Project Name>`) per bound project.

Currently, when a project is archived, its forum tag remains in `channel.available_tags` indefinitely. Over time, as projects are created and archived, this exhausts the remaining 12 tag slots, causing subsequent project creation or tag synchronization to fail with Discord 400 errors. Removing the archived project's forum tag immediately frees the slot for active projects while preserving task identity through thread title prefixes (`[PREFIX-X]`).

## What Changes

- **Tag Removal on Project Archive**: When a project is archived, its corresponding `📁 <Project Name>` forum tag is automatically deleted from `channel.available_tags` in the bound forum channel.
- **Orphan Tag Pruning**: When updating or rebuilding forum control hubs, tags associated with already-archived projects are pruned to recover previously leaked tag slots.
- **Tag Restoration on Unarchive**: When an archived project is restored, its forum tag is re-created (if slots remain under the 20-tag limit) and re-applied to active unarchived task threads. If the forum channel is full (>= 20 tags), a non-blocking warning is surfaced.

## Capabilities

### New Capabilities
- `forum-tag-lifecycle`: Manages forum channel tag allocation, removal upon project archival, and restoration upon unarchival within Discord's 20-tag limit.

### Modified Capabilities
*(None - no prior OpenSpec specs exist in the repository)*

## Impact

- **Adapters**:
  - `src/adapters/discord_bot/views/forum_helpers.py`: Add `remove_project_tag(forum_channel, project_name)` and enhance `ensure_pinned_hub_post` to prune tags of known archived projects.
  - `src/adapters/discord_bot/views/project/archive.py`: Invoke tag removal during project archive confirmation.
  - `src/adapters/discord_bot/views/project/restore.py`: Ensure graceful handling and user notification if tag creation fails due to tag capacity.
- **API & Dependencies**: No external package additions; uses existing `discord.py` forum channel editing APIs.
- **Database**: No schema changes required.
