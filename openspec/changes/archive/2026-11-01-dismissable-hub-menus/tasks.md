# Tasks

## 1. Add Close Action Buttons to Ephemeral Views

- [x] 1.1 Add Close button (`style=discord.ButtonStyle.secondary`, `label="Close"`, `row=0`) and `_on_close_clicked` callback to `TaskMenuView` in `src/adapters/discord_bot/views/task_menu.py`, and verify with unit tests that it deletes the ephemeral card.
- [x] 1.2 Add Close button (`style=discord.ButtonStyle.secondary`, `label="Close"`, `row=0`) and `_on_close_clicked` callback to `ProjectMenuView` in `src/adapters/discord_bot/views/project/dashboard.py`, and verify with unit tests that it deletes the ephemeral card.
- [x] 1.3 Add Close button (`style=discord.ButtonStyle.secondary`, `label="Close"`, `row=0`) and `_on_close_clicked` callback to `TechTreeViewer` in `src/adapters/discord_bot/views/tree_view.py`, and verify with unit tests that it deletes the ephemeral card.

## 2. Public Interaction Safety Guardrails & Verification

- [x] 2.1 Audit `PmHubView` in `src/adapters/discord_bot/views/hub_menu.py` to verify no component interactions on the public post register into `MenuSessionManager.register_menu()`.
- [x] 2.2 Add runtime guard to `MenuSessionManager.register_menu()` in `src/adapters/discord_bot/menu_manager.py` rejecting non-ephemeral message interactions.
- [x] 2.3 Add unit tests in `tests/test_menu_manager.py` verifying that `MenuSessionManager.register_menu()` rejects non-ephemeral interactions.
- [x] 2.4 Add architectural fitness test in `tests/test_architecture.py` using AST parsing to assert that `PmHubView` methods never call `register_menu`.
- [x] 2.5 Add unit tests in `tests/test_hub_menu_dismissal.py` verifying that closing `TaskMenuView`, `ProjectMenuView`, and `TechTreeViewer` executes deletion against the ephemeral interaction and leaves any public message references intact.

## 3. Integration & Full Check

- [x] 3.1 Run targeted test suite (`uv run pytest tests/test_hub_menu_dismissal.py tests/test_menus.py tests/test_tree_view.py`) and verify all tests pass.
- [x] 3.2 Run full check (`make check` or `uv run ruff check . && uv run ruff format --check . && uv run pytest -v tests/`) to ensure linting, formatting, and all test suites pass.
