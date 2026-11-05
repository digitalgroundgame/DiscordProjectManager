# Proposal

## Why

Interactive ephemeral views opened from the Forum Control Hub—specifically **"My Tasks"** (`TaskMenuView`), **"Projects Hub"** (`ProjectMenuView`), and **"Visual Graph"** (`TechTreeViewer`)—currently stay permanently in the user's Discord client unless manually dismissed via Discord's low-contrast client-side link. Furthermore, attempting to use naive `delete_original_response()` on parent public interactions risks deleting the public Control Hub post itself. Users need a prominent, safe, 1-click **Close** button on each of these three ephemeral menus that deletes only the private ephemeral message.

## What Changes

- Add a standardized **Close** action button (`style=discord.ButtonStyle.secondary`, label `"Close"`, `row=0`) to [`TaskMenuView`](file:///home/espdesign/git/DiscordProjectManager/src/adapters/discord_bot/views/task_menu.py).
- Add a standardized **Close** action button (`style=discord.ButtonStyle.secondary`, label `"Close"`, `row=0`) to [`ProjectMenuView`](file:///home/espdesign/git/DiscordProjectManager/src/adapters/discord_bot/views/project/dashboard.py).
- Add a standardized **Close** action button (`style=discord.ButtonStyle.secondary`, label `"Close"`, `row=0`) to [`TechTreeViewer`](file:///home/espdesign/git/DiscordProjectManager/src/adapters/discord_bot/views/tree_view.py).
- Implement safe ephemeral message dismissal: when `Close` is clicked, the callback acknowledges the interaction on that ephemeral message and deletes it without touching the public Forum Control Hub.
- Add an automated runtime guard to `MenuSessionManager.register_menu()` that rejects interactions originating from public messages (`interaction.message.flags.ephemeral is False`), preventing accidental public post deletions.
- Add an architectural fitness check in `tests/test_architecture.py` using AST analysis to assert that `PmHubView` never invokes `register_menu`.
- For `TechTreeViewer` (ephemeral followup), store the `discord.WebhookMessage` reference to enable direct, safe deletion upon `Close` or timeout without referencing the parent public interaction.

## Capabilities

### New Capabilities
- `hub-menu-dismissal`: Explicit 1-click dismissal controls and lifecycle safety for ephemeral menus spawned from the Forum Control Hub.

### Modified Capabilities
<!-- None: existing capabilities remain unchanged. -->

## Impact

- **Adapters (`src/adapters/discord_bot/views/`)**:
  - `task_menu.py`: Add `Close` button and callback to `TaskMenuView`.
  - `project/dashboard.py`: Add `Close` button and callback to `ProjectMenuView`.
  - `tree_view.py`: Add `Close` button, callback, and optional webhook message reference to `TechTreeViewer`.
  - `hub_menu.py`: Pass `wait=True` when sending visual graph followup to attach webhook message reference to viewer.
- **Tests**: Add unit tests for `Close` button presence, button emoji compliance, and safe ephemeral deletion across `TaskMenuView`, `ProjectMenuView`, and `TechTreeViewer`.
