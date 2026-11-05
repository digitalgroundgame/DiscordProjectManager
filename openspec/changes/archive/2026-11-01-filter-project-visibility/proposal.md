# Proposal

## Why

Currently, when users open the Active Projects roster (via the Projects Hub button on the forum control hub) or execute `/pm project list`, all active projects across the Discord server are displayed regardless of user roles or squad assignments. Additionally, project autocomplete suggestions leak private project names across teams. Projects restricted to dedicated squads must be hidden from unauthorized members to prevent cross-team information leaks and maintain confidential project boundaries.

## What Changes

- Scope active project listing in the **Project Management Hub** (`ProjectMenuView._on_list_projects_clicked`) so that only projects the caller has permission to view are rendered in the `ProjectActiveListView`.
- Provide clear, graceful ephemeral feedback when a user has permission to view zero active projects in the server.
- Scope `/pm project list` slash command output so it only includes projects the user is authorized to view.
- Scope `project_autocomplete` across `/pm` slash commands so users only receive autocomplete suggestions for projects they have permission to view.
- Enhance `AuthService` with a high-performance batch filtering method (`filter_visible_projects`) to prevent N+1 database queries when checking permissions for project lists.

## Capabilities

### New Capabilities
- `project-visibility-authorization`: Enforces role-based visibility scoping for active projects across interactive UI rosters, slash commands, and autocompletion.

### Modified Capabilities
<!-- No existing spec requirements modified -->

## Impact

- **Adapters**:
  - `src/adapters/discord_bot/views/project/dashboard.py` (`ProjectMenuView`)
  - `src/adapters/discord_bot/views/project/roster.py` (`ProjectActiveListView`)
  - `src/adapters/discord_bot/cogs/project_cog.py` (`ProjectCommandsMixin.project_list`)
  - `src/adapters/discord_bot/cogs/autocomplete.py` (`AutocompleteMixin.project_autocomplete`)
- **Services**:
  - `src/services/auth_service.py` (`AuthService`)
- **Tests**:
  - `tests/test_authorization.py`
