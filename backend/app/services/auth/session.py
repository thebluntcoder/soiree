"""
services/auth/session.py — Soirée's own login session (distinct from Swiggy).

After phone-OTP verification the user gets an opaque session token, stored
in Redis for 30 days:

    soiree_session:{token} → {"user_id": ..., "phone": ...}

The frontend sends it as `X-Soiree-Session` on every request; `deps.current_user`
resolves it to a `User` row. The user's Swiggy OAuth token is keyed by
`user_id` (see endpoints/auth.py), so one login owns one Swiggy connection.
"""

import json
import secrets

from app.core.redis import get_redis

SESSION_TTL_SECONDS = 30 * 24 * 3600  # 30 days
_KEY = "soiree_session:{token}"


async def create_session(user_id: str, phone: str) -> str:
    token = secrets.token_urlsafe(32)
    redis = await get_redis()
    await redis.setex(
        _KEY.format(token=token),
        SESSION_TTL_SECONDS,
        json.dumps({"user_id": user_id, "phone": phone}),
    )
    return token


async def resolve_session(token: str | None) -> dict | None:
    """{'user_id', 'phone'} for a valid token, else None."""
    if not token:
        return None
    redis = await get_redis()
    raw = await redis.get(_KEY.format(token=token))
    return json.loads(raw) if raw else None


async def revoke_session(token: str | None) -> None:
    if not token:
        return
    redis = await get_redis()
    await redis.delete(_KEY.format(token=token))
