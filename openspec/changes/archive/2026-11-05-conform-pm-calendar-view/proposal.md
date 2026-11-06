# Proposal

## Why

The `/pm calendar` command generates an ephemeral view (`CalendarFeedView`) with user-specific RFC 5545 calendar subscription URLs. Unlike the project's other ephemeral views (`TaskMenuView`, `ProjectMenuView`, `TechTreeViewer`), `CalendarFeedView` currently lacks an explicit "Close" button, is never registered with `MenuSessionManager` for automatic single-session lifecycle management, fails to refresh its interaction context upon token regeneration (risking timeout exceptions), and bypasses `DiscordViewRouter`. This causes orphaned ephemeral cards and inconsistent UX across the bot's slash command suite.

## What Changes

- Add a standardized secondary "Close" button (`discord.ButtonStyle.secondary`, label `"Close"`, `row=0`) to `CalendarFeedView` that deletes the ephemeral response and unregisters from `MenuSessionManager`.
- Register the ephemeral interaction in `MenuSessionManager.register_menu(interaction)` when `/pm calendar` is invoked in `src/adapters/discord_bot/cogs/pm_cog.py`.
- Refactor `/pm calendar` invocation to route through `DiscordViewRouter.build_calendar_feed_view(...)` rather than directly instantiating the view and embed in `pm_cog.py`.
- Update `self._initial_interaction` and synchronize with `MenuSessionManager` during `_on_regenerate_clicked` to ensure `BaseView.on_timeout` operates against a fresh interaction token.
- Add unit tests verifying button layout, dismiss callback, timeout unregistration, and router delegation.

## Capabilities

### New Capabilities
<!-- None -->

### Modified Capabilities
- `hub-menu-dismissal`: Add `CalendarFeedView Dismissal Control` requirement specifying that the calendar subscription view must provide an explicit Close button that immediately deletes the ephemeral card and stops the view session.

## Impact

- `src/adapters/discord_bot/views/calendar_feed_view.py`: Adds Close button, updates `self._initial_interaction` on regenerate.
- `src/adapters/discord_bot/cogs/pm_cog.py`: Routes through `DiscordViewRouter` and registers with `MenuSessionManager`.
- `src/adapters/discord_bot/menu_manager.py`: Ensures interaction replacement/update for active sessions.
- `tests/test_calendar_feed.py` and `tests/test_hub_menu_dismissal.py`: Additional test coverage for Close button and lifecycle management.
