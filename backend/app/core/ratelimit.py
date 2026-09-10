"""
core/ratelimit.py — a small Redis fixed-window rate limiter.

The plan endpoints each make 1-2 Claude calls. Without a limit, an
unauthenticated loop runs up the Anthropic bill. This caps requests per
caller per window.

Caller identity: the Soirée login session if present, else the Swiggy
session id, else the client IP (best-effort — respects X-Forwarded-For's
first hop, which Railway sets).

Fail-open: if Redis is unavailable the request is allowed (better than a
hard outage). The limiter is a FastAPI dependency:

    @router.post("/generate", dependencies=[Depends(rate_limit("plan", 20, 3600))])
"""

import logging
import time

from fastapi import Header, HTTPException, Request

from app.core.redis import get_redis

logger = logging.getLogger(__name__)


def _client_id(request: Request, session_id: str | None) -> str:
    if session_id:
        return f"s:{session_id}"
    fwd = request.headers.get("x-forwarded-for", "")
    ip = fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")
    return f"ip:{ip}"


def rate_limit(name: str, limit: int, window_seconds: int):
    """Build a dependency that allows `limit` requests per `window_seconds`."""

    async def _dep(
        request: Request,
        x_session_id: str | None = Header(None, alias="X-Session-ID"),
        x_soiree_session: str | None = Header(None, alias="X-Soiree-Session"),
    ) -> None:
        who = _client_id(request, x_soiree_session or x_session_id)
        bucket = int(time.time()) // window_seconds
        key = f"rl:{name}:{who}:{bucket}"
        try:
            redis = await get_redis()
            count = await redis.incr(key)
            if count == 1:
                await redis.expire(key, window_seconds)
        except Exception as e:  # noqa: BLE001 — fail open
            logger.warning("rate limiter unavailable (%s) — allowing", e)
            return
        if count > limit:
            retry = window_seconds - (int(time.time()) % window_seconds)
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit: {limit} per {window_seconds // 60} min. "
                f"Try again in {retry}s.",
                headers={"Retry-After": str(retry)},
            )

    return _dep
