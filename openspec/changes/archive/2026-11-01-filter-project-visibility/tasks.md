# Tasks

## 1. Service Layer Batch Filtering

- [x] 1.1 Implement `filter_visible_projects(user, projects)` and support `Project | UUID` in `can_view_project` on `AuthService`. Verify with unit tests in `tests/test_authorization.py`.
- [x] 1.2 Add unit test coverage in `tests/test_authorization.py` for server manager bypass, project lead access, squad role membership, and public projects.

## 2. Interactive Roster & Control Hub Flow

- [x] 2.1 Update `ProjectMenuView._on_list_projects_clicked` in `src/adapters/discord_bot/views/project/dashboard.py` to filter projects with `auth_service.filter_visible_projects` and send an ephemeral error toast when 0 projects are accessible. Verify with mock interaction test in `tests/test_authorization.py`.
- [x] 2.2 Ensure `auth_service` is passed into `ProjectActiveListView` and propagated back to `build_project_menu` upon "Back to Project Menu" click in `src/adapters/discord_bot/views/project/roster.py`.

## 3. Slash Command & Autocomplete Scoping

- [x] 3.1 Update `ProjectCommandsMixin.project_list` in `src/adapters/discord_bot/cogs/project_cog.py` to filter active projects and handle the empty accessible projects state. Verify with test in `tests/test_authorization.py`.
- [x] 3.2 Update `AutocompleteMixin.project_autocomplete` in `src/adapters/discord_bot/cogs/autocomplete.py` to filter project suggestions using `auth_service.filter_visible_projects`. Verify with autocomplete test in `tests/test_authorization.py`.

## 4. Verification & Architectural Fitness

- [x] 4.1 Run `make check` (`ruff check .`, `ruff format --check .`, and `pytest tests/`) to ensure no regressions or architecture boundary violations.
