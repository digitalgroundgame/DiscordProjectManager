# Design: Standardized Permission Toasts

## Context
See `proposal.md` for motivation. PR #108 created `send_toast`, `build_toast_embed`, and `attach_dismissal_notice` to unify ephemeral notifications into color-coded embeds with countdown timers. However, earlier permission checks in forum hubs, tree views, and project menus remained implemented with raw `interaction.response.send_message` and hardcoded status emojis.

## Goals / Non-Goals

**Goals:**
- Unify all visual graph, project creation, and project visibility denial notices to use `send_toast(interaction, message, toast_type=ToastType.ERROR)`.
- Apply Discord blockquote styling (`> `) to secondary requirement/guidance text across all permission error toasts.
- Eliminate raw emoji clutter (`❌`) in favor of standard red embed borders.
- Automatically present the dynamic countdown timer (`*Auto-dismisses <t:{timestamp}:R>*`) via `attach_dismissal_notice`.

**Non-Goals:**
- Modifying underlying authorization rules or `AuthService` evaluation logic.
- Altering ephemeral menu lifetimes or non-toast views.

## Decisions

### Decision 1: Use `send_toast` over manual `build_toast_embed` + `schedule_toast_dismissal`
- **Choice**: Call `await send_toast(interaction, message, toast_type=ToastType.ERROR)` directly.
- **Rationale**: `send_toast` automatically handles interaction state (unresponded vs deferred), builds the embed with countdown footer, sends with `ephemeral=True`, and schedules background toast dismissal via `menu_manager`.
- **Alternatives considered**: Manually building the embed with `build_toast_embed` and calling `schedule_toast_dismissal`. Rejected because it duplicates response dispatch logic.

### Decision 2: Consistent Blockquote Formatting for Secondary Requirements
- **Choice**: Structure multi-line permission notices as:
  ```python
  f"You do not have permission to view the visual graph for **{target_project.name}**.\n"
  "> You must hold the project squad's Discord role, be the Project Lead, or be a server manager."
  ```
- **Rationale**: Keeps the primary refusal bold and clear on line 1, while indenting the prerequisites/next actions as a readable contextual blockquote on line 2.

## Risks / Trade-offs

- **[Existing unit tests assert raw text strings on `inter.response.send_message`]** → Update `tests/test_authorization.py` to assert the embed description on `inter.response.send_message` (or mock `send_toast`).
