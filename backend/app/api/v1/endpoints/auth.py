"""
api/v1/endpoints/auth.py — OAuth 2.1 PKCE authentication endpoints.

FLOW:
  1. Frontend calls GET /api/v1/auth/start
     → Backend generates PKCE pair + state
     → Stores (code_verifier, state) in Redis (2min TTL)
     → Returns Swiggy authorize URL to frontend

  2. Frontend redirects user to Swiggy authorize URL
     → User logs in with phone + OTP on Swiggy's page
     → Swiggy redirects to https://soiree-blue.vercel.app/auth/callback?code=...&state=...

  3. Frontend /auth/callback page calls POST /api/v1/auth/callback
     → Backend retrieves code_verifier from Redis using state
     → Exchanges code for access_token with Swiggy
     → Stores token in Redis with session_id (5 day TTL)
     → Returns session_id to frontend

  4. Frontend stores session_id in localStorage
     → Sends session_id with every plan generation request
     → Backend retrieves Bearer token from Redis for MCP calls

  5. GET /api/v1/auth/status?session_id=...
     → Returns whether session has valid token
     → Frontend shows "Connected as [phone]" or "Connect Swiggy" button

  6. POST /api/v1/auth/logout?session_id=...
     → Calls Swiggy /auth/logout to revoke token
     → Deletes token from Redis

DYNAMIC CLIENT REGISTRATION:
  client_id is obtained once via /auth/register and cached in Redis.
  On startup, if no client_id in Redis, we register fresh.
  This is transparent — Swiggy MCP supports DCR automatically.
"""

import secrets
import time
import json
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse, JSONResponse
from pydantic import BaseModel

from app.core.redis import get_redis
from app.services.auth.oauth import (
    generate_pkce,
    build_authorize_url,
    exchange_code_for_token,
    register_client,
    token_redis_key,
    pkce_redis_key,
    is_token_expired,
    REDIRECT_URI,
)

router = APIRouter()

# Redis key for cached client_id from Dynamic Client Registration
CLIENT_ID_KEY = "swiggy_oauth_client_id"

# PKCE state TTL — 2 minutes (code expires in 120s per Swiggy docs)
PKCE_TTL = 120

# Token TTL — 5 days (432000s per Swiggy docs)
TOKEN_TTL = 432000


async def get_or_register_client_id() -> str:
    """
    Get cached client_id from Redis, or register a new client if not found.

    Dynamic Client Registration happens once per deployment.
    The client_id is cached in Redis indefinitely — no expiry.

    Returns:
        client_id string from Swiggy DCR
    """
    redis = await get_redis()

    # Check cache first
    cached = await redis.get(CLIENT_ID_KEY)
    if cached:
        return cached

    # Register new client with Swiggy
    try:
        registration = await register_client()
        client_id = registration["client_id"]
        # Cache indefinitely — client_id doesn't expire
        await redis.set(CLIENT_ID_KEY, client_id)
        return client_id
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"Failed to register OAuth client with Swiggy: {str(e)}",
        )


@router.get("/start", summary="Start OAuth flow — returns Swiggy authorize URL")
async def auth_start():
    """
    Generate PKCE challenge and return the Swiggy authorize URL.

    Frontend redirects the user to this URL. User logs in with
    their Swiggy phone number + OTP on Swiggy's consent page.

    Returns:
        {
          "authorize_url": "https://mcp.swiggy.com/auth/authorize?...",
          "state": "<random_state>"
        }
    """
    redis = await get_redis()

    # Generate PKCE pair
    code_verifier, code_challenge = generate_pkce()

    # Generate random state for CSRF protection
    state = secrets.token_urlsafe(16)

    # Store verifier in Redis — needed when code comes back in callback
    # TTL: 120 seconds (Swiggy authorization code expires in 120s)
    await redis.setex(
        pkce_redis_key(state),
        PKCE_TTL,
        json.dumps({"code_verifier": code_verifier, "state": state}),
    )

    # Get or register client_id
    client_id = await get_or_register_client_id()

    # Build Swiggy authorize URL
    authorize_url = build_authorize_url(
        code_challenge=code_challenge,
        state=state,
        client_id=client_id,
    )

    return {
        "authorize_url": authorize_url,
        "state": state,
        "message": "Redirect user to authorize_url to complete Swiggy login",
    }


class CallbackRequest(BaseModel):
    code: str
    state: str


@router.post("/callback", summary="Handle OAuth callback — exchange code for token")
async def auth_callback(request: CallbackRequest):
    """
    Exchange the authorization code for an access token.

    Called by the frontend /auth/callback page after Swiggy
    redirects back with ?code=...&state=...

    Args:
        code: authorization code from Swiggy
        state: must match what we sent in /auth/start (CSRF check)

    Returns:
        {
          "session_id": "<uuid>",
          "expires_at": <unix_timestamp>,
          "message": "Connected to Swiggy"
        }
    """
    redis = await get_redis()

    # Retrieve stored PKCE data using state (CSRF + verifier lookup)
    pkce_data_raw = await redis.get(pkce_redis_key(request.state))
    if not pkce_data_raw:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired state. Please restart the login flow.",
        )

    pkce_data = json.loads(pkce_data_raw)
    code_verifier = pkce_data["code_verifier"]

    # Delete PKCE data — authorization code is single-use
    await redis.delete(pkce_redis_key(request.state))

    # Exchange code for access token
    try:
        token_response = await exchange_code_for_token(
            code=request.code,
            code_verifier=code_verifier,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Token exchange failed: {str(e)}")

    # Generate session_id for this user's session
    session_id = secrets.token_urlsafe(32)

    # Calculate expiry timestamp
    expires_in = token_response.get("expires_in", TOKEN_TTL)
    expires_at = time.time() + expires_in

    # Store token in Redis with session_id as key
    # TTL matches token expiry (5 days)
    await redis.setex(
        token_redis_key(session_id),
        expires_in,
        json.dumps(
            {
                "access_token": token_response["access_token"],
                "expires_at": expires_at,
                "scope": token_response.get("scope", "mcp:tools"),
            }
        ),
    )

    return {
        "session_id": session_id,
        "expires_at": expires_at,
        "message": "Connected to Swiggy successfully",
    }


@router.get("/status", summary="Check if session has valid Swiggy token")
async def auth_status(session_id: str = Query(...)):
    """
    Check if a session has a valid, non-expired Swiggy access token.

    Frontend calls this on load to show "Connected" or "Connect Swiggy" UI.

    Returns:
        { "authenticated": bool, "expires_at": float | None }
    """
    redis = await get_redis()

    token_data_raw = await redis.get(token_redis_key(session_id))
    if not token_data_raw:
        return {"authenticated": False, "expires_at": None}

    token_data = json.loads(token_data_raw)
    expires_at = token_data.get("expires_at", 0)

    if is_token_expired(expires_at):
        # Token expired — delete it and tell frontend to re-auth
        await redis.delete(token_redis_key(session_id))
        return {"authenticated": False, "expires_at": None, "reason": "expired"}

    return {
        "authenticated": True,
        "expires_at": expires_at,
    }


@router.post("/logout", summary="Revoke Swiggy token and clear session")
async def auth_logout(session_id: str = Query(...)):
    """
    Revoke the Swiggy access token and clear the session from Redis.
    """
    import httpx
    from app.services.auth.oauth import LOGOUT_URL

    redis = await get_redis()

    token_data_raw = await redis.get(token_redis_key(session_id))
    if token_data_raw:
        token_data = json.loads(token_data_raw)
        access_token = token_data.get("access_token")

        # Revoke token with Swiggy
        if access_token:
            try:
                async with httpx.AsyncClient() as client:
                    await client.post(
                        LOGOUT_URL, headers={"Authorization": f"Bearer {access_token}"}
                    )
            except Exception:
                pass  # Best-effort revocation — still clear local session

        # Always delete from Redis
        await redis.delete(token_redis_key(session_id))

    return {"message": "Logged out successfully"}


async def get_access_token(session_id: str) -> str | None:
    """
    Helper: retrieve access token for a session_id.

    Called by MCP clients to get the Bearer token for API calls.
    Returns None if session is invalid or token is expired.

    On 401 from Swiggy MCP: call this — if returns None, redirect to /auth/start.
    On 419 from Swiggy MCP: full re-auth needed (phone + OTP again).
    """
    if not session_id:
        return None

    redis = await get_redis()
    token_data_raw = await redis.get(token_redis_key(session_id))
    if not token_data_raw:
        return None

    token_data = json.loads(token_data_raw)
    expires_at = token_data.get("expires_at", 0)

    if is_token_expired(expires_at):
        await redis.delete(token_redis_key(session_id))
        return None

    return token_data.get("access_token")
