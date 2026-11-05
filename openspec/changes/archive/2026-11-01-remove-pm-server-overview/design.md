# Design

## Context

See `proposal.md` for problem motivation and background.

In [`src/adapters/discord_bot/views/admin_menu.py`](file:///home/espdesign/git/DiscordProjectManager/src/adapters/discord_bot/views/admin_menu.py):
- `PmDashboardView` instantiates `self.overview_btn` labeled "Server Overview" and binds it to `_on_overview_clicked`.
- `build_pm_dashboard_embed()` includes "Server Overview" descriptions in the action bullet lists for both server managers and regular contributors.
- `_on_overview_clicked` loads guild projects and squashes them into a single embed field, exceeding Discord's 1,024-character limit and throwing `discord.HTTPException: 400 Bad Request (50035)`.
- `PmDashboardOverviewView` provides a back button to return to `PmDashboardView`. It is currently used both by `_on_overview_clicked` and `_on_guides_clicked`.

## Goals / Non-Goals

**Goals:**
- Eliminate the 50035 API error by removing `self.overview_btn` and `_on_overview_clicked` from `PmDashboardView`.
- Clean up descriptions in `build_pm_dashboard_embed()`.
- Maintain `PmDashboardOverviewView` so `_on_guides_clicked` and related backward-compatible imports remain fully intact.
- Update test suites to verify the cleaned `/pm menu` button layout.

**Non-Goals:**
- Building a new server-wide metrics engine (if desired in the future, a proper metrics service can be scoped separately).
- Changing `ProjectActiveListView` or `ProjectMenuView` behavior.

## Decisions

### Decision 1: Direct button removal from `PmDashboardView`
- **Choice**: Remove `self.overview_btn` and `_on_overview_clicked` directly.
- **Alternatives Considered**:
  - *Keep button and chunk embed*: Rejected because `Projects` -> `Active Projects` already provides a paginated, searchable, role-scoped roster.
  - *Keep a stub toast*: Unnecessary clutter; the button is redundant.

### Decision 2: Retain `PmDashboardOverviewView` for Guides
- **Choice**: Keep `PmDashboardOverviewView` in `admin_menu.py`.
- **Rationale**: `_on_guides_clicked` renders the command guide using `PmDashboardOverviewView` to provide a "Back to Control Center" button. Keeping the class avoids breaking guides or any legacy re-exports.

## Risks / Trade-offs

- **[Risk] Existing open ephemeral menus**: Old ephemeral menu instances generated before the bot restart will have the previous button layout.
  - **Mitigation**: Ephemeral menus have standard timeouts (`EPHEMERAL_MENU_TIMEOUT_SECONDS`) and auto-expire. Clicking an expired interaction safely fails on Discord's side.
- **[Risk] Test breakage**: Unit tests in `tests/test_menus.py` specifically assert `dash_view._on_overview_clicked`.
  - **Mitigation**: Update test assertions to test the current button set (`new_project_btn`, `lead_roles_btn`, `projects_btn`, `settings_btn`, `guides_btn`).
