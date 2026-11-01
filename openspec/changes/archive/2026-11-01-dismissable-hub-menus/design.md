# Design

## Context

Users accessing "My Tasks", "Projects Hub", and "Visual Graph" from the Forum Control Hub (`PmHubView`) receive ephemeral interactive views. Currently, none of these views feature an in-band dismiss/close button, forcing users to rely on Discord's small client-side dismiss prompt.

Crucially, Discord's API has a dangerous behavior: calling `delete_original_response()` on a component interaction that originated from a public message deletes the public message itself. Therefore, all dismissal mechanisms must operate strictly on interactions originating from the ephemeral message itself.

## Goals / Non-Goals

**Goals:**
- Provide a standardized, prominent `[Close]` button on row 0 across `TaskMenuView`, `ProjectMenuView`, and `TechTreeViewer`.
- Ensure clicking `Close` deletes only the private ephemeral message and stops view execution.
- Maintain absolute safety for public channel messages (e.g., the pinned Control Hub post `PmHubView` and thread starter cards).
- Comply with the workspace Button & Component Emoji Policy (`style=secondary`, no decorative emojis).

**Non-Goals:**
- Altering the business logic, filtering, or query mechanisms inside the views.
- Changing the public pinned Hub post structure.

## Decisions

### 1. Close Button Placement and Style
- **Buttons**:
  - `TaskMenuView`: Add `self.close_btn = discord.ui.Button(label="Close", style=discord.ButtonStyle.secondary, row=0)`
  - `ProjectMenuView`: Add `self.close_btn = discord.ui.Button(label="Close", style=discord.ButtonStyle.secondary, row=0)`
  - `TechTreeViewer`: Add `self.close_btn = discord.ui.Button(label="Close", style=discord.ButtonStyle.secondary, row=0)`
- **Rationale**: Keeps the dismiss action immediately visible on row 0 across all three menus while adhering to the rule: "Never add emojis to buttons purely for decoration if a clear text label suffices."

### 2. Interaction Deletion Scope
- In `_on_close_clicked(interaction)`:
  ```python
  self.stop()
  try:
      if not interaction.response.is_done():
          await interaction.response.defer()
      await interaction.delete_original_response()
  except Exception as e:
      logger.debug("Failed to delete ephemeral menu on close: %s", e)
  ```
- **Rationale**: Because the user clicked the `Close` button located *on* the ephemeral message, `interaction` is scoped directly to that ephemeral card. Calling `delete_original_response()` on this interaction deletes only the ephemeral message. It cannot affect the public Control Hub message.

### 3. Public Message Isolation & MenuSessionManager Hygiene
- **Rule**: Do not register `PmHubView` button clicks into `MenuSessionManager.register_menu(interaction)`.
- **Reasoning**: `MenuSessionManager` calls `delete_original_response()` on previous interactions. Registering public component interactions causes Discord to delete the public message.
- **Runtime Guard**: In `MenuSessionManager.register_menu(interaction)`:
  ```python
  msg = getattr(interaction, "message", None)
  if msg and not getattr(getattr(msg, "flags", None), "ephemeral", False):
      logger.warning("Rejected attempt to register public message interaction %s in MenuSessionManager", interaction.id)
      return
  ```
- **Architectural Fitness Check**: Add an AST test to `tests/test_architecture.py` verifying that methods inside `PmHubView` never contain calls to `register_menu`.

## Risks / Trade-offs

- **[Risk] User manually dismisses ephemeral message via Discord client before clicking Close**
  - *Mitigation*: The `_on_close_clicked` callback wraps `delete_original_response()` in `try ... except Exception: pass` to safely handle already-dismissed messages without crashing or throwing errors.
