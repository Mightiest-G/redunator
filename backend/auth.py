"""Redunator backend — device-scoped JWT auth (added 2026-11-18).

The app has no user accounts, so identity is a per-device JWT:

1. The app generates a UUID ``device_id`` once (and stores it on the
   device) and calls ``POST /auth/device`` with it.
2. The backend returns a bearer token (HS256, 30-day expiry).
3. The app sends ``Authorization: Bearer <token>`` on every user
   endpoint (``/user_report``, ``/user_alerts``, ``/user_feedback``,
   ``/user_settings``, ``/subscriptions/verify``).

The token's ``sub`` claim IS the user id. No header (``X-User-Id``)
and no ``user_id`` field in a request body is ever trusted for
identity — the pre-2026-11-18 header scheme was spoofable by
anyone who set the header, and is gone.

Configuration
-------------
``REDUNATOR_JWT_SECRET`` (env var) is the HS256 signing secret. It
is read at request time, not import time, so a deploy can set it
without a code change. It must be a long random value (32+ bytes);
never commit it, never print it, never put it in a URL.

- If the secret is unset, ``POST /auth/device`` answers 503 with
  reason ``jwt_secret_not_configured`` (same fallback pattern as
  ``GET /app/latest``) and every protected endpoint answers 401 —
  the backend fails closed, never open.
- Rotating the secret invalidates every outstanding token at once
  (devices simply mint a fresh one on next launch — there is no
  server-side token store to migrate).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Header, HTTPException

ISSUER = "redunator"
TOKEN_TTL_SECONDS = 30 * 24 * 3600  # 30 days


class InvalidTokenError(ValueError):
    """Raised when a presented device token cannot be trusted."""


def get_secret() -> str:
    """HS256 secret from the environment ("" when not configured)."""
    return os.environ.get("REDUNATOR_JWT_SECRET", "")


def issue_device_token(device_id: str) -> tuple[str, int]:
    """Mint a device token. ``device_id`` is normalised to canonical
    UUID form first, so two spellings of one device are one user.
    Returns (token, expires_in_seconds). Raises ValueError when the
    secret is not configured or the device id is not a UUID."""
    try:
        device_id = str(uuid.UUID(str(device_id)))
    except (ValueError, AttributeError, TypeError):
        raise ValueError("device_id must be a UUID")
    secret = get_secret()
    if not secret:
        raise ValueError("REDUNATOR_JWT_SECRET is not configured")
    now = datetime.now(timezone.utc)
    payload = {
        "sub": device_id,
        "iss": ISSUER,
        "iat": now,
        "exp": now + timedelta(seconds=TOKEN_TTL_SECONDS),
    }
    return jwt.encode(payload, secret, algorithm="HS256"), TOKEN_TTL_SECONDS


def verify_device_token(token: str) -> str:
    """Return the device_id (``sub``) for a valid token, or raise
    InvalidTokenError for an expired, mis-signed, wrong-issuer,
    malformed, or sub-less token — and when no secret is configured
    (verification without a secret can never succeed)."""
    secret = get_secret()
    if not secret:
        raise InvalidTokenError("device auth is not configured")
    if not token or not isinstance(token, str):
        raise InvalidTokenError("missing token")
    try:
        payload = jwt.decode(
            token, secret, algorithms=["HS256"], issuer=ISSUER,
            options={"require": ["exp", "iss", "sub"]},
        )
    except jwt.ExpiredSignatureError:
        raise InvalidTokenError("token expired")
    except jwt.InvalidTokenError as e:
        raise InvalidTokenError(f"invalid token: {e}")
    return str(payload["sub"])


def require_device(
    authorization: str | None = Header(None, alias="Authorization"),
) -> str:
    """FastAPI dependency: the device_id behind a valid Bearer token.

    Missing / malformed / expired / mis-signed token -> 401 with the
    standard {"error": "unauthorized", ...} body (via the app's
    StarletteHTTPException handler, which passes dict details
    through unchanged)."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail={"error": "unauthorized",
                    "message": "Missing or malformed Authorization: "
                               "Bearer <token> header."},
        )
    try:
        return verify_device_token(authorization[len("Bearer "):].strip())
    except InvalidTokenError as e:
        raise HTTPException(
            status_code=401,
            detail={"error": "unauthorized", "message": str(e)},
        )
