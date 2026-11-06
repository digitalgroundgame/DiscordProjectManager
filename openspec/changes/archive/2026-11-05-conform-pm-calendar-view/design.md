# Design

## Context

Ephemeral menus in the Discord bot adapter follow a strict lifecycle contract managed by `BaseView` and `MenuSessionManager` (`src/adapters/discord_bot/menu_manager.py`). See `proposal.md` for motivation. Currently, `CalendarFeedView` in `src/adapters/discord_bot/views/calendar_feed_view.py` is instantiated directly by `PmCog.calendar` without registering the interaction in `MenuSessionManager` or providing a Close button, creating inconsistent UX and potential timeout cleanup failures.

## Goals / Non-Goals

**Goals:**
- Provide a 1-click Close button (`discord.ButtonStyle.secondary`, label `"Close"`, `row=0`) on `CalendarFeedView`.
- Register `/pm calendar` ephemeral interaction in `MenuSessionManager.register_menu(interaction)` to maintain single-menu user state.
- Route view instantiation in `pm_cog.py` through `DiscordViewRouter.build_calendar_feed_view(...)`.
- Ensure token regeneration in `_on_regenerate_clicked` refreshes `self._initial_interaction` and syncs with `MenuSessionManager` so timeout dismissal targets the active interaction token.

**Non-Goals:**
- Modifying iCalendar RFC 5545 rendering, VTODO generation, or token generation cryptography in `CalendarService`.
- Altering the behavior or lifecycle of public forum posts or other existing views.

## Decisions

### Decision 1: Button Placement in Row 0
Place `close_button` on `row=0` alongside the universal `.ics` link button and `Regenerate Link` button:
```python
# Direct link button
ics_button = discord.ui.Button(
    label="Open Calendar Feed (.ics)", style=discord.ButtonStyle.link, url=urls["https_url"], row=0
)
# Regenerate button
regen_button = discord.ui.Button(
    label="Regenerate Link", style=discord.ButtonStyle.danger, custom_id="calendar_feed_regenerate", row=0
)
# Close button
close_button = discord.ui.Button(label="Close", style=discord.ButtonStyle.secondary, row=0)
```
*Rationale*: All 3 buttons fit cleanly in a single action row (under Discord's 5-button max per row limit) matching other compact ephemeral views (`TechTreeViewer`).
*Alternatives considered*: Placing Close on row 1 — rejected as unnecessary vertical whitespace expansion.

### Decision 2: Interaction Context Refresh on Regenerate
In `CalendarFeedView._on_regenerate_clicked(interaction)`:
```python
self._initial_interaction = interaction
get_menu_manager().update_interaction(interaction)
```
*Rationale*: Discord interaction tokens expire after 15 minutes. When a user clicks Regenerate after several minutes, Discord issues a fresh interaction token. Resetting `_initial_interaction` and updating `MenuSessionManager._active_menus` guarantees `BaseView.on_timeout` executes against a valid token rather than an expired one.
*Alternatives considered*: Relying solely on the original slash command interaction token — rejected because it risks silent timeout cleanup failures when regenerate is clicked late in the session.

### Decision 3: Close Button Handler Behavior
In `CalendarFeedView._on_close_clicked(interaction)`:
```python
self.stop()
try:
    if hasattr(self, "_initial_interaction") and self._initial_interaction:
        get_menu_manager().unregister_menu(self._initial_interaction)
    get_menu_manager().unregister_menu(interaction)
    if not interaction.response.is_done():
        await interaction.response.defer()
    await interaction.delete_original_response()
except Exception as e:
    logger.debug("Failed to delete calendar feed view on close: %s", e)
```
*Rationale*: Matches the exact dismissal logic implemented in `TechTreeViewer`, `TaskMenuView`, and `ProjectMenuView`, ensuring graceful session unregistration and message deletion.

### Decision 4: Cog Routing via View Router
In `src/adapters/discord_bot/cogs/pm_cog.py`:
Replace direct `CalendarFeedView` and `build_calendar_feed_embed` instantiation with:
```python
embed, view = await DiscordViewRouter.build_calendar_feed_view(
    calendar_service=self.calendar_service,
    user=interaction.user,
    initial_interaction=interaction,
)
await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
await get_menu_manager().register_menu(interaction)
```
*Rationale*: Satisfies Hexagonal Architecture router pattern, avoiding circular imports and maintaining unified view factory abstractions.

## Risks / Trade-offs

- [Risk] Interaction token expires before user interacts.
  → *Mitigation*: Handled gracefully: `BaseView.on_timeout()` defaults to `EPHEMERAL_MENU_TIMEOUT_SECONDS` (810 seconds / 13.5 minutes), which is 10% shorter than Discord's 900-second token TTL, with outer `try/except` capturing `NotFound` errors.
- [Risk] Menu manager registration attempted on public messages.
  → *Mitigation*: Protected by `MenuSessionManager`'s runtime check (`interaction.message.flags.ephemeral`) and `ephemeral=True` in `send_message`.
