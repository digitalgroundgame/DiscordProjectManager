"""Stateless HMAC-SHA256 session token signing and verification for the web portal."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any

SESSION_COOKIE_NAME = "dgg_pm_session"
OAUTH_STATE_COOKIE_NAME = "dgg_pm_oauth_state"
DEFAULT_MAX_AGE_SECONDS = 86400 * 7  # 7 days
OAUTH_STATE_MAX_AGE_SECONDS = 600  # 10 minutes


def create_session_token(
    payload: dict[str, Any],
    secret_key: str,
    max_age: int = DEFAULT_MAX_AGE_SECONDS,
) -> str:
    """Signs a session dictionary into a URL-safe HMAC-SHA256 token."""
    data = dict(payload)
    now = int(time.time())
    data.setdefault("iat", now)
    data["exp"] = now + max_age

    raw_json = json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")
    body_b64 = base64.urlsafe_b64encode(raw_json).decode("ascii").rstrip("=")
    sig = hmac.new(
        secret_key.encode("utf-8"),
        body_b64.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"{body_b64}.{sig}"


def verify_session_token(token: str | None, secret_key: str) -> dict[str, Any] | None:
    """Verifies an HMAC-SHA256 session token and returns its payload if valid and unexpired."""
    if not token or "." not in token:
        return None
    parts = token.split(".")
    if len(parts) != 2:
        return None
    body_b64, sig = parts
    if not body_b64 or not sig:
        return None

    expected_sig = hmac.new(
        secret_key.encode("utf-8"),
        body_b64.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        return None

    padding = "=" * (-len(body_b64) % 4)
    try:
        raw_json = base64.urlsafe_b64decode((body_b64 + padding).encode("ascii"))
        data = json.loads(raw_json.decode("utf-8"))
    except Exception:
        return None

    if not isinstance(data, dict):
        return None

    exp = data.get("exp")
    if not isinstance(exp, (int, float)) or exp <= time.time():
        return None

    return data
