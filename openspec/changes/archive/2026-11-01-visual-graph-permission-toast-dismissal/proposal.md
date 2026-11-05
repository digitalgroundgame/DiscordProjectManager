# Proposal

## Why

When an unauthorized user attempts to view a project's visual tech tree graph or clicks restricted project management actions on the Forum Control Hub, the error message is sent ephemerally without auto-dismissal. This leaves an error message stuck on the user's screen indefinitely until dismissed manually, inconsistent with other self-dismissing ephemeral feedback across the bot.

## What Changes

- Schedule auto-dismissal via `menu_manager.schedule_toast_dismissal` for the single-project visual graph permission error in `PmHubView.tree_tab`.
- Schedule auto-dismissal for the multi-project zero-accessible visual graph permission notice in `PmHubView.tree_tab`.
- Schedule auto-dismissal for the create-project permission denial in `PmHubView.create_project_btn`.
- Schedule auto-dismissal for the visual graph permission notice in `ProjectMenuView._on_tech_tree_clicked`.
- Schedule auto-dismissal for visual graph project selection and layout toggle permission denials in `TreeView`.

## Capabilities

### New Capabilities
<!-- None -->

### Modified Capabilities
- `project-visibility-authorization`: Require standard ephemeral toast auto-dismissal for all visual graph and hub permission-denied alerts.

## Impact

- **Adapters**:
  - `src/adapters/discord_bot/views/hub_menu.py` (`PmHubView`)
  - `src/adapters/discord_bot/views/project/dashboard.py` (`ProjectMenuView`)
  - `src/adapters/discord_bot/views/tree_view.py` (`TreeView`, `TechTreeProjectSelectView`)
- **Tests**:
  - `tests/test_authorization.py`
