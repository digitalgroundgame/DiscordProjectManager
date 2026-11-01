# Tasks

## 1. Views Refactoring

- [x] 1.1 Convert `PmHubView` permission notices (`tech_tree_tab`, `create_project_btn`, `new_task_btn`) to use `send_toast` with `ToastType.ERROR` and blockquote formatting, and verify with unit tests.
- [x] 1.2 Convert `ProjectMenuView` permission notices (`_on_tech_tree_clicked`, `_on_list_projects_clicked`) to use `send_toast` with `ToastType.ERROR` and blockquote formatting, and verify with unit tests.
- [x] 1.3 Convert `TechTreeViewer` and `TechTreeProjectSelectView` permission notices (`_rerender`, `_on_select_project`) to use `send_toast` with `ToastType.ERROR` and blockquote formatting, and verify with unit tests.

## 2. Test Updates & Verification

- [x] 2.1 Update `tests/test_authorization.py` to assert that permission denial responses produce red toast embeds containing blockquote guidance and countdown timers. Verify with `direnv exec . uv run pytest tests/test_authorization.py`.
- [x] 2.2 Run full lint, format, and test checks (`direnv exec . uv run ruff check . && direnv exec . uv run ruff format --check . && direnv exec . uv run pytest tests/`).
