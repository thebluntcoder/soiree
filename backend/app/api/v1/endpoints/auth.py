"""
api/v1/endpoints/auth.py — connect a logged-in Soirée user's Swiggy account.

Soirée login (phone OTP, see endpoints/users.py) comes first. These
endpoints then link that user to a Swiggy MCP access token via OAuth 2.1
PKCE. The token is stored in Redis keyed by the Soirée `user.id` — one
login owns exactly one Swiggy connection — so the frontend only ever
sends `X-Soiree-Session`, never a separate Swiggy session id.

FLOW:
  1. GET  /api/v1/auth/start      (X-Soiree-Session)
     → PKCE pair + state, {code_verifier, state, user_id} cached in Redis
     → returns Swiggy authorize URL

  2. user authenticates on Swiggy's page → redirect to
     REDIRECT_URI?code=...&state=...

  3. POST /api/v1/auth/callback   (X-Soiree-Session) {code, state}
     → verify state, verify it was this same user who started
     → exchange code → store encrypted token at swiggy_token:{user_id}

  4. GET  /api/v1/auth/status     (X-Soiree-Session)
     → { connected: bool, expires_at: float | None }

  5. POST /api/v1/auth/logout     (X-Soiree-Session)
     → revoke token with Swiggy, delete swiggy_token:{user_id}

On 401 from Swiggy MCP: token is dead — call get_access_token again, and
if it returns None send the user back through /auth/start.
"""

import json
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.v1.deps import current_user
from app.core.crypto import decrypt, encrypt
from app.core.redis import get_redis
from app.models.user import User
from app.services.auth.oauth import (
    build_authorize_url,
    exchange_code_for_token,
    generate_pkce,
    is_token_expired,
    pkce_redis_key,
    register_client,
    token_redis_key,
)

router = APIRouter()

# Redis key for cached client_id from Dynamic Client Registration
CLIENT_ID_KEY = "swiggy_oauth_client_id"

# PKCE state TTL — 2 minutes (code expires in 120s per Swiggy docs)
PKCE_TTL = 120

# Token TTL — 5 days (432000s per Swiggy docs)
TOKEN_TTL = 432000


async def get_or_register_client_id() -> str:
    """Cached client_id from Redis, or a fresh Dynamic Client Registration."""
    redis = await get_redis()

    cached = await redis.get(CLIENT_ID_KEY)
    if cached:
        return cached

    try:
        registration = await register_client()
        client_id = registration["client_id"]
        await redis.set(CLIENT_ID_KEY, client_id)
        return client_id
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=503,
            detail=f"Failed to register OAuth client with Swiggy: {str(e)}",
        )


@router.get("/start", summary="Start linking the current user's Swiggy account")
async def auth_start(user: User = Depends(current_user)):
    """
    Generate a PKCE challenge and return the Swiggy authorize URL.

    The frontend redirects the user to `authorize_url`; they log in with
    their Swiggy phone + OTP on Swiggy's consent page.
    """
    redis = await get_redis()

    code_verifier, code_challenge = generate_pkce()
    state = secrets.token_urlsafe(16)

    # Stash the verifier AND the user id — only the user who started the
    # flow may finish it (see /auth/callback).
    await redis.setex(
        pkce_redis_key(state),
        PKCE_TTL,
        json.dumps(
            {"code_verifier": code_verifier, "state": state, "user_id": user.id}
        ),
    )

    client_id = await get_or_register_client_id()
    authorize_url = build_authorize_url(
        code_challenge=code_challenge, state=state, client_id=client_id
    )

    return {
        "authorize_url": authorize_url,
        "state": state,
        "message": "Redirect user to authorize_url to complete Swiggy login",
    }


class CallbackRequest(BaseModel):
    code: str
    state: str


@router.post("/callback", summary="Finish linking — exchange code for a token")
async def auth_callback(
    request: CallbackRequest, user: User = Depends(current_user)
):
    """Exchange the authorization code and store the token for this user."""
    redis = await get_redis()

    pkce_data_raw = await redis.get(pkce_redis_key(request.state))
    if not pkce_data_raw:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired state. Please restart the login flow.",
        )

    pkce_data = json.loads(pkce_data_raw)
    # Authorization code is single-use — burn the PKCE record now.
    await redis.delete(pkce_redis_key(request.state))

    if pkce_data.get("user_id") != user.id:
        raise HTTPException(
            status_code=403,
            detail="This Swiggy login was started by a different session.",
        )

    code_verifier = pkce_data["code_verifier"]

    try:
        token_response = await exchange_code_for_token(
            code=request.code, code_verifier=code_verifier
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Token exchange failed: {str(e)}")

    expires_in = token_response.get("expires_in", TOKEN_TTL)
    expires_at = time.time() + expires_in

    await redis.setex(
        token_redis_key(user.id),
        expires_in,
        json.dumps(
            {
                "access_token": encrypt(token_response["access_token"]),
                "expires_at": expires_at,
                "scope": token_response.get("scope", "mcp:tools"),
            }
        ),
    )

    return {"connected": True, "expires_at": expires_at, "message": "Connected to Swiggy"}


@router.get("/status", summary="Is the current user's Swiggy account connected?")
async def auth_status(user: User = Depends(current_user)):
    """{ connected: bool, expires_at: float | None }"""
    redis = await get_redis()

    token_data_raw = await redis.get(token_redis_key(user.id))
    if not token_data_raw:
        return {"connected": False, "expires_at": None}

    token_data = json.loads(token_data_raw)
    expires_at = token_data.get("expires_at", 0)

    if is_token_expired(expires_at):
        await redis.delete(token_redis_key(user.id))
        return {"connected": False, "expires_at": None, "reason": "expired"}

    return {"connected": True, "expires_at": expires_at}


@router.post("/logout", summary="Disconnect the current user's Swiggy account")
async def auth_logout(user: User = Depends(current_user)):
    """Revoke the Swiggy token and forget it."""
    import httpx

    from app.services.auth.oauth import LOGOUT_URL

    redis = await get_redis()

    token_data_raw = await redis.get(token_redis_key(user.id))
    if token_data_raw:
        token_data = json.loads(token_data_raw)
        access_token = decrypt(token_data.get("access_token") or "")

        if access_token:
            try:
                async with httpx.AsyncClient() as client:
                    await client.post(
                        LOGOUT_URL, headers={"Authorization": f"Bearer {access_token}"}
                    )
            except Exception:  # noqa: BLE001 — best-effort revocation
                pass

        await redis.delete(token_redis_key(user.id))

    return {"message": "Disconnected from Swiggy"}


async def get_access_token(user_id: str) -> str | None:
    """
    The Swiggy MCP Bearer token for a Soirée user, or None if they have
    not connected Swiggy / the token has expired.

    Called by the plan, search and orchestrator layers. On 401 from Swiggy
    MCP: call this again — None means send the user through /auth/start.
    """
    if not user_id:
        return None

    redis = await get_redis()
    token_data_raw = await redis.get(token_redis_key(user_id))
    if not token_data_raw:
        return None

    token_data = json.loads(token_data_raw)
    expires_at = token_data.get("expires_at", 0)

    if is_token_expired(expires_at):
        await redis.delete(token_redis_key(user_id))
        return None

    return decrypt(token_data.get("access_token") or "") or None


async def purge_swiggy_token(user_id: str) -> None:
    """Delete a user's stored Swiggy token — used by account deletion."""
    if not user_id:
        return
    redis = await get_redis()
    await redis.delete(token_redis_key(user_id))
