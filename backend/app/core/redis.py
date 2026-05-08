"""
redis.py — Async Redis client and connection management.

CONCEPT: What is Redis and why do we need it?
----------------------------------------------
Redis is an in-memory key-value store — think of it as a super-fast
dictionary that lives outside your app process and survives restarts.

We use Redis for three distinct purposes in Soirée:

  1. CACHING MCP responses
     Swiggy restaurant data doesn't change every second. Instead of hitting
     the Dineout MCP on every plan generation, we cache results with a TTL
     (time-to-live). Restaurant list: 1hr TTL. Menus: 30min. Offers: 5min
     (offers change fast — we never want to show a stale deal).

  2. SESSION STORAGE
     JWT tokens for logged-in users. Redis lets us invalidate sessions
     instantly (logout, security breach) — something you can't do with
     stateless JWTs alone.

  3. CELERY BROKER
     Celery (our background job system) needs a "broker" — a message queue
     where it puts tasks. Redis is the simplest and most common choice.
     When a user approves a plan, we push an "place_order" task to Redis,
     and a Celery worker picks it up asynchronously.

CONCEPT: Singleton pattern
--------------------------
We use a module-level `_redis_client` variable (a singleton) so we create
the connection pool ONCE when the app starts, not on every request.
Creating a new Redis connection on every API call would be slow and
wasteful — pools exist to be reused.
"""

import redis.asyncio as aioredis
from app.core.config import settings

# Module-level singleton — None until first get_redis() call.
# Underscore prefix (_) is a Python convention meaning "private to this module".
_redis_client: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    """
    Return the shared Redis client, creating it on first call (lazy init).

    CONCEPT: Lazy initialisation
    ----------------------------
    We don't create the Redis connection at import time because the config
    (REDIS_URL) might not be loaded yet. Instead we create it on the first
    actual request — "lazy" because we defer work until it's needed.

    from_url() creates a connection pool under the hood. All async Redis
    operations (get, set, delete) are non-blocking — the event loop can
    handle other requests while waiting for Redis to respond.

    Usage in an endpoint:
        redis = await get_redis()
        cached = await redis.get("restaurant:mumbai")
        await redis.set("restaurant:mumbai", data, ex=3600)  # ex = TTL in seconds
    """
    global _redis_client
    if _redis_client is None:
        # Railway provides rediss:// (SSL) or redis:// — both work with from_url
        _redis_client = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            ssl_cert_reqs=None,  # Railway Redis uses self-signed cert
        )
    return _redis_client


async def close_redis() -> None:
    """
    Cleanly close the Redis connection on application shutdown.

    Called from the lifespan() context manager in main.py.
    Without this, the connection would be left open and the process
    might hang during shutdown.
    """
    global _redis_client
    if _redis_client:
        await _redis_client.close()
        _redis_client = None
