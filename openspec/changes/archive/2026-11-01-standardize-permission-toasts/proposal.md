# Proposal: Standardize Permission Toast Presentation and Lifecycle

## Why

Permission denial messages across the Forum Control Hub, Project Menu, and Visual Graph Viewer currently send unstyled raw markdown text via `interaction.response.send_message` with hardcoded `❌` emojis. While auto-dismissal was recently scheduled in the background, users do not see the dynamic Discord relative countdown timer (`*Auto-dismisses <t:{timestamp}:R>*`), lack blockquote formatting (`> `) for secondary guidance, and miss the semantic color-coded embed cards established in PR #108 / ADR 0007.

Standardizing these notices to use `send_toast` with `ToastType.ERROR` and blockquote styling eliminates interface drift and provides clear, consistent, and polished feedback.

## What Changes

- **Standardize Visual Graph & Hub Permission Messages**:
  - Replace raw `interaction.response.send_message` calls with `send_toast(interaction, ..., toast_type=ToastType.ERROR)`.
  - Format multi-line permission notices using blockquote styling (`> `) for secondary requirement explanations.
  - Remove redundant `❌` leading emojis, allowing the red embed status color bar to indicate the error state cleanly.
  - Ensure the dynamic Discord relative countdown timer (`*Auto-dismisses <t:{timestamp}:R>*`) is automatically attached to the embed description.
- **Affected Views & Handlers**:
  - `PmHubView.tech_tree_tab`: Single project permission denial and zero accessible projects feedback.
  - `PmHubView.create_project_btn`: Project creation permission denial.
  - `ProjectMenuView._on_tech_tree_clicked`: Zero accessible visual graphs notice.
  - `ProjectMenuView._on_list_projects_clicked`: Zero accessible active projects notice.
  - `TechTreeViewer._rerender`: Visual graph orientation switch permission denial.
  - `TechTreeProjectSelectView._on_select_project`: Project selection permission denial in visual graph picker.

## Capabilities

### Modified Capabilities
- `project-visibility-authorization`: Update requirement `Ephemeral toast auto-dismissal for visual graph and hub permission feedback` to mandate standardized error toast embeds (`ToastType.ERROR`), dynamic relative countdown timers, and blockquote styling for secondary requirement explanations.

## Impact

- `src/adapters/discord_bot/views/hub_menu.py`: Refactored to use `send_toast` for permission notices.
- `src/adapters/discord_bot/views/project/dashboard.py`: Refactored to use `send_toast` for permission notices.
- `src/adapters/discord_bot/views/tree_view.py`: Refactored to use `send_toast` for permission notices.
- `tests/test_authorization.py`: Updated test assertions to inspect embed descriptions, blockquote formatting, and toast structures.
