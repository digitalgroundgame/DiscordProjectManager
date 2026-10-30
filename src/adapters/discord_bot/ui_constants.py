"""Unified design language and lifecycle constants for ephemeral Discord interactions.

Standardizes durations, timeouts, and component aesthetics across ephemeral menus,
confirmation dialogs, and toast messages to prevent interface drift.
"""

from __future__ import annotations

import discord

# ---------------------------------------------------------------------------
# Interaction & Ephemeral Lifecycles
# ---------------------------------------------------------------------------

# Discord interaction tokens are valid for up to 15 minutes (900s).
DISCORD_INTERACTION_MAX_SECONDS: float = 900.0

# Active ephemeral menus time out 10% before Discord's maximum 15-minute token limit (810s / 13.5m)
# ensuring clean unregistration and deletion before interaction tokens expire.
EPHEMERAL_MENU_TIMEOUT_SECONDS: float = DISCORD_INTERACTION_MAX_SECONDS * 0.90

# Standard ephemeral toast auto-dismissal delay for normal confirmations and updates (6s).
TOAST_DURATION_STANDARD: float = 6.0

# Extended ephemeral toast auto-dismissal delay for notices requiring additional reading or error detail (10s).
TOAST_DURATION_EXTENDED: float = 10.0

# Text prefix for ephemeral dismissal countdown notices.
TOAST_DISMISS_PREFIX: str = "Auto-dismisses"

# ---------------------------------------------------------------------------
# Interactive Dialog Button Design Language
# ---------------------------------------------------------------------------
# Standardized button styling and ordering for confirmation and draft dialogs:
# Primary action on the left (index 0, Success/Danger), Cancel/Discard on the right (index 1, Secondary).

BUTTON_LABEL_SAVE: str = "Save Changes"
BUTTON_LABEL_DISCARD: str = "Discard Changes"
BUTTON_LABEL_CANCEL: str = "Cancel"

BUTTON_STYLE_PRIMARY: discord.ButtonStyle = discord.ButtonStyle.primary
BUTTON_STYLE_CONFIRM: discord.ButtonStyle = discord.ButtonStyle.success
BUTTON_STYLE_SUCCESS: discord.ButtonStyle = discord.ButtonStyle.success
BUTTON_STYLE_DANGER: discord.ButtonStyle = discord.ButtonStyle.danger
BUTTON_STYLE_SECONDARY: discord.ButtonStyle = discord.ButtonStyle.secondary
