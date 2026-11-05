# Design

## Context

See `proposal.md` for motivation. Currently, `AuthService.can_view_project(user, project_id)` exists and correctly enforces access controls for tech tree visualization. However, project listing in `ProjectMenuView._on_list_projects_clicked`, `ProjectCommandsMixin.project_list`, and `AutocompleteMixin.project_autocomplete` calls `project_service.list_projects()` directly without filtering against user permissions.

## Goals / Non-Goals

**Goals:**
- Provide a high-performance batch authorization check `filter_visible_projects(user, projects)` in `AuthService`.
- Filter project listing in `ProjectMenuView` (active projects roster) and `/pm project list`.
- Filter project name suggestions in `AutocompleteMixin.project_autocomplete`.
- Provide clear and actionable ephemeral feedback when an authenticated user has zero visible projects.

**Non-Goals:**
- Altering the pinned forum control hub embed (`build_hub_welcome_embed`), which is channel-scoped and public to anyone with channel access.
- Restricting `can_manage_projects` administrative dashboard views (`/pm dashboard`).

## Decisions

### Decision 1: Batch Filter Helper on `AuthService`
- **Choice**: Implement `filter_visible_projects(user: ActorPrincipal | Any, projects: list[Project]) -> list[Project]` on `AuthService`, and update `can_view_project` to accept `Project | UUID`.
- **Rationale**: Calling `can_view_project(user, p.id)` in a loop incurs an N+1 query penalty (calling `get_by_id` and `list_squads_for_project` for every project). A batch helper can short-circuit immediately for server managers (0 DB queries) and reuse already-fetched `Project` objects.
- **Alternatives Considered**:
  - *Looping `can_view_project` at the call site*: Causes 2N queries for N projects.
  - *Filtering in SQL queries*: Requires passing caller role IDs down into repository layer, coupling DB layer with Discord caller security context and violating hexagonal separation.

### Decision 2: Triggering Visibility Check in Hub on "Active Projects" Click
- **Choice**: Allow regular members to open `ProjectMenuView` (where they see `Active Projects`, `Visual Graph`, and `PM Main Menu`), and perform the project visibility evaluation when they click `Active Projects`.
- **Rationale**: Keeps the control hub button interaction fast and responsive without performing auth checks on the initial hub button click. If the user clicks `Active Projects` and has 0 accessible projects, an informative ephemeral message is displayed.

### Decision 3: Preserving `auth_service` across Navigation
- **Choice**: Pass `auth_service` through `ProjectMenuView` into `ProjectActiveListView`, and back to `build_project_menu` when the user clicks "Back to Project Menu".
- **Rationale**: Prevents dropping the authorization context during view navigation transitions.

## Risks / Trade-offs

- **[Performance on Large Project Counts]** → Mitigated by batch evaluation in `filter_visible_projects` and instant server manager bypass.
- **[Ephemeral State on Back Navigation]** → Mitigated by passing `auth_service` through `ProjectActiveListView`.
