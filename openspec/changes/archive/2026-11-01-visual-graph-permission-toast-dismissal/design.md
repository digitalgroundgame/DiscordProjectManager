# Design

## Context

See `proposal.md` for motivation. In `PmHubView`, `ProjectMenuView`, and `TreeView`, permission checks send ephemeral messages via `interaction.response.send_message(..., ephemeral=True)`. However, unlike task and project mutating views, these visual graph and hub entrypoints do not call `menu_manager.schedule_toast_dismissal(interaction, delay=TOAST_DURATION_STANDARD)`.

## Goals / Non-Goals

**Goals:**
- Ensure all visual graph and hub action permission denial alerts auto-dismiss after `TOAST_DURATION_STANDARD` (6.0s).
- Maintain consistency across `PmHubView`, `ProjectMenuView`, and `TreeView`.

**Non-Goals:**
- Refactoring the entire codebase's alert system from raw `send_message` to `send_toast` embeds; this is an ad-hoc fix targeting visual graph and hub permission feedback.

## Decisions

### Decision 1: Use `menu_manager.schedule_toast_dismissal` on Ephemeral Message Responses
- **Choice**: Call `menu_manager.schedule_toast_dismissal(interaction, delay=TOAST_DURATION_STANDARD)` immediately following each `interaction.response.send_message(..., ephemeral=True)` in:
  1. `PmHubView.tree_tab` (single-project check)
  2. `PmHubView.tree_tab` (multi-project zero-accessible check)
  3. `PmHubView.create_project_btn` (create project permission check)
  4. `ProjectMenuView._on_tech_tree_clicked` (zero-accessible check)
  5. `TreeView._on_change_project` (project switch check)
  6. `TreeView._on_toggle_layout` (layout orientation toggle check)
- **Rationale**: This is the established, battle-tested pattern used in other interactive menus in `src/adapters/discord_bot/views/` (e.g. `_on_list_projects_clicked`, `new_task_btn`).

## Risks / Trade-offs

- **[Interaction Expiration]** → `schedule_toast_dismissal` catches Discord `NotFound` and `Forbidden` errors internally, safely handling expired or dismissed interactions.
