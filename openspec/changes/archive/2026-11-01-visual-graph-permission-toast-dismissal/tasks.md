# Tasks

## 1. Control Hub & Dashboard Permission Dismissals

- [x] 1.1 In `src/adapters/discord_bot/views/hub_menu.py`, schedule toast dismissal using `menu_manager.schedule_toast_dismissal` for single-project and multi-project visual graph permission denials and create project denial. Verify with tests in `tests/test_authorization.py`.
- [x] 1.2 In `src/adapters/discord_bot/views/project/dashboard.py`, schedule toast dismissal for visual graph zero-accessible project denial in `_on_tech_tree_clicked`. Verify with test in `tests/test_authorization.py`.

## 2. Tech Tree Interactive Viewer Dismissals

- [x] 2.1 In `src/adapters/discord_bot/views/tree_view.py`, schedule toast dismissal for project switch and layout toggle permission denials. Verify with test in `tests/test_authorization.py`.

## 3. Verification & Architectural Fitness

- [x] 3.1 Run `make check` (`ruff check .`, `ruff format --check .`, and `pytest tests/`) to verify all checks pass.
