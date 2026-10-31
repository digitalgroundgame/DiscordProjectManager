# Tasks

## 1. Forum Tag Helper Functions

- [x] 1.1 Implement `remove_project_tag(forum_channel, project_name)` in `src/adapters/discord_bot/views/forum_helpers.py` to remove matching project tags from `channel.available_tags`, and verify with unit tests in `tests/test_forum_helpers.py`.
- [x] 1.2 Implement `prune_archived_project_tags(forum_channel, archived_project_names)` helper to batch-remove orphaned tags for archived projects, and verify with unit tests in `tests/test_forum_helpers.py`.

## 2. Project Archival and Restoration Integration

- [x] 2.1 Update `ProjectArchiveConfirmView` in `src/adapters/discord_bot/views/project/archive.py` to invoke `remove_project_tag` when archiving a project bound to a forum channel, and verify with integration tests in `tests/adapters/discord_bot/views/test_project_archive.py`.
- [x] 2.2 Update `ensure_pinned_hub_post` in `src/adapters/discord_bot/views/forum_helpers.py` to detect and prune forum tags of known archived projects during hub reconciliation, and verify with test cases.
- [x] 2.3 Verify `ProjectRestoreConfirmView` in `src/adapters/discord_bot/views/project/restore.py` handles tag limit errors gracefully with user-facing warnings when `ensure_project_tag` reports capacity exhaustion.

## 3. Verification & Architecture Checks

- [x] 3.1 Run test suite with `uv run pytest tests/` to verify zero regression across existing workspace and forum helper tests.
- [x] 3.2 Run `make check` (ruff linter, formatter check, and test suite) to ensure code compliance with project quality standards.
