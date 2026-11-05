# Proposal

## Why

The `/pm menu` Project Management Control Center currently includes a "Server Overview" button. Clicking this button triggers a Discord API error (`400 Bad Request, error code 50035: In embeds.0.fields.0.value: Must be 1024 or fewer in length`) whenever a server has multiple active projects, because it attempts to pack all active projects with mentions into a single embed field.

Furthermore, this button is redundant and leaky:
1. The `/pm menu` root embed already summarizes active projects and open tasks count.
2. The "Projects" -> "Active Projects" view (`ProjectActiveListView`) already provides a paginated (8 per page), searchable, and role-authorized list of projects.
3. The "Server Overview" callback bypassed project visibility rules, exposing private projects to unauthorized contributors before crashing.

Removing the "Server Overview" button eliminates the crash, prevents unauthorized project leakage, and directs users to the dedicated, paginated "Projects" roster.

## What Changes

- Remove the "Server Overview" button (`overview_btn`) and its registration from `PmDashboardView` in `src/adapters/discord_bot/views/admin_menu.py`.
- Remove the `_on_overview_clicked` interaction callback from `PmDashboardView`.
- Remove the "Server Overview" action bullet points from `Management Actions` and `Available Workspace Actions` in `build_pm_dashboard_embed`.
- Update tests in `tests/test_menus.py` and `tests/test_base_view.py` to remove checks and clicks for the "Server Overview" button.
- Retain `PmDashboardOverviewView` as the back-navigable container for the "Guides" view to maintain architectural stability.

## Capabilities

### New Capabilities
- `pm-control-center`: Defines the action layout and navigation flow for the `/pm menu` Control Center dashboard, ensuring reliable, permission-safe workspace management.

### Modified Capabilities

## Impact

- **Discord Bot UI**: Users opening `/pm menu` see clean, functional buttons: "Create Project", "Team Leads", "Projects", "My Settings", and "Guides".
- **Source Code**: Simplifies `src/adapters/discord_bot/views/admin_menu.py`.
- **Test Suite**: Updates existing menu and view tests in `tests/test_menus.py` and `tests/test_base_view.py`.
