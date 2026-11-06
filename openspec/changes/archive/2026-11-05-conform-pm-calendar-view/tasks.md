# Tasks

## 1. CalendarFeedView Close Button & Timeout Context

- [x] 1.1 Add secondary `Close` button (`style=discord.ButtonStyle.secondary`, `label="Close"`, `row=0`) and `_on_close_clicked` handler in `src/adapters/discord_bot/views/calendar_feed_view.py`, and verify with unit tests in `tests/test_calendar_feed.py` and `tests/test_hub_menu_dismissal.py`.
- [x] 1.2 Update `self._initial_interaction` and synchronize with `MenuSessionManager` in `CalendarFeedView._on_regenerate_clicked`, and verify with unit tests in `tests/test_calendar_feed.py`.

## 2. Slash Command Routing & Session Registration

- [x] 2.1 Refactor `/pm calendar` in `src/adapters/discord_bot/cogs/pm_cog.py` to delegate to `DiscordViewRouter.build_calendar_feed_view(...)` and remove direct view/embed imports.
- [x] 2.2 Register the ephemeral interaction with `MenuSessionManager.register_menu(interaction)` in `/pm calendar` in `pm_cog.py`, and verify with unit tests in `tests/test_calendar_feed.py`.

## 3. Verification & Architectural Compliance

- [x] 3.1 Run full suite of calendar feed, menu manager, and hub dismissal unit tests with `direnv exec . uv run pytest tests/test_calendar_feed.py tests/test_hub_menu_dismissal.py tests/test_menu_manager.py`.
- [x] 3.2 Run lint, formatting, and architectural fitness checks with `direnv exec . uv run ruff check .` and `direnv exec . uv run pytest tests/test_architecture.py`.
