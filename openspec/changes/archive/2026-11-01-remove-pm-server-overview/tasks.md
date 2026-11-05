# Tasks

## 1. Clean Up /pm Menu Views and Embed

- [x] 1.1 Remove `self.overview_btn` and `_on_overview_clicked` from `PmDashboardView` in `src/adapters/discord_bot/views/admin_menu.py`, verifying syntax and imports with `uv run ruff check src/adapters/discord_bot/views/admin_menu.py`.
- [x] 1.2 Remove the "Server Overview" bullet points from `Management Actions` and `Available Workspace Actions` in `build_pm_dashboard_embed` in `src/adapters/discord_bot/views/admin_menu.py`, verifying the embed renders without "Server Overview".

## 2. Test Suite Updates and Verification

- [x] 2.1 Update `tests/test_menus.py` to remove `_on_overview_clicked` invocation and verify `PmDashboardView` instantiates without `overview_btn`, running `uv run pytest tests/test_menus.py -k "test_pm_dashboard"`.
- [x] 2.2 Run full project validation (`make check`) to verify all tests pass and ruff formatting/linting succeeds cleanly.
