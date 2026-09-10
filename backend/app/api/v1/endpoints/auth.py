"""
api/v1/endpoints/auth.py — Swiggy OAuth *is* the Soirée login.

There is no separate account. Signing in means authorising Soirée against
your Swiggy account (OAuth 2.1 PKCE); the Swiggy MCP access token is a JWT
whose `sub` claim identifies you. From that we get-or-create a Soirée
`User` and mint a 30-day Soirée session.

FLOW:
  1. GET  /api/v1/auth/start            (public)
     → PKCE pair + state, {code_verifier, state} cached in Redis (2 min)
     → returns the Swiggy authorize URL

  2. user authenticates on Swiggy's page → redirect to
     REDIRECT_URI?code=...&state=...

  3. POST /api/v1/auth/callback         (public) {code, state}
     → verify state, exchange code for the token
     → decode the token → sub → get-or-create User
     → store the encrypted token at swiggy_token:{user_id}
     → mint a Soirée session
     → { soiree_session, user, is_new, swiggy_expires_at }

  4. GET  /api/v1/auth/status           (X-Soiree-Session)
     → { connected: bool, expires_at }  — is the Swiggy token still live?

  5. POST /api/v1/auth/logout           (X-Soiree-Session)
     → revoke the Swiggy token + the Soirée session

The Swiggy token lasts 5 days with no refresh. When it lapses the Soirée
session may still be valid, but MCP calls fail — the frontend sees
`connected: false` from /auth/status and sends the user back through
/auth/start (one tap if Swiggy still has them logged in).
"""

import json
import secrets
import time
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.api.v1.deps import current_user
from app.core.crypto import decrypt, encrypt
from app.core.database import get_session
from app.core.ratelimit import rate_limit
from app.core.redis import get_redis
from app.models.user import User
from app.schemas.user import AuthResponse, UserRead
from app.services.auth.oauth import (
    TokenIdentityError,
    build_authorize_url,
    decode_token_identity,
    exchange_code_for_token,
    generate_pkce,
    is_token_expired,
    pkce_redis_key,
    register_client,
    token_redis_key,
)
from app.services.auth.session import create_session, revoke_session

router = APIRouter()

CLIENT_ID_KEY = "swiggy_oauth_client_id"
PKCE_TTL = 120           # Swiggy authorization code expires in 120s
TOKEN_TTL = 432000       # 5 days — fallback if the token response omits expires_in

_START_LIMIT = Depends(rate_limit("auth_start", limit=30, window_seconds=3600))
_CALLBACK_LIMIT = Depends(rate_limit("auth_callback", limit=30, window_seconds=3600))


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
            detail=f"Failed to register OAuth client with Swiggy: {e}",
        )


@router.get("/start", summary="Begin sign-in — returns the Swiggy authorize URL",
            dependencies=[_START_LIMIT])
async def auth_start():
    """
    Generate a PKCE challenge and return the Swiggy authorize URL. The
    frontend redirects the user there to log in on Swiggy's own page.
    Public — there is no Soirée user yet.
    """
    redis = await get_redis()

    code_verifier, code_challenge = generate_pkce()
    state = secrets.token_urlsafe(16)

    await redis.setex(
        pkce_redis_key(state),
        PKCE_TTL,
        json.dumps({"code_verifier": code_verifier, "state": state}),
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


@router.post("/callback", response_model=AuthResponse,
             summary="Finish sign-in — exchange code, create session",
             dependencies=[_CALLBACK_LIMIT])
async def auth_callback(
    request: CallbackRequest, db: AsyncSession = Depends(get_session)
):
    """Exchange the code, identify the user from the token, start a session."""
    redis = await get_redis()

    pkce_data_raw = await redis.get(pkce_redis_key(request.state))
    if not pkce_data_raw:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired state. Please restart sign-in.",
        )
    # Authorization code is single-use — burn the PKCE record now.
    await redis.delete(pkce_redis_key(request.state))
    code_verifier = json.loads(pkce_data_raw)["code_verifier"]

    try:
        token_response = await exchange_code_for_token(
            code=request.code, code_verifier=code_verifier
        )
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Token exchange failed: {e}")

    access_token = token_response["access_token"]
    try:
        identity = decode_token_identity(access_token)
    except TokenIdentityError as e:
        raise HTTPException(
            status_code=502,
            detail=f"Couldn't read your Swiggy identity from the token ({e}).",
        )

    result = await db.execute(
        select(User).where(User.swiggy_sub == identity["sub"])
    )
    user = result.scalar_one_or_none()
    is_new = user is None
    if is_new:
        user = User(swiggy_sub=identity["sub"], swiggy_user_id=identity["user_id"])
    else:
        user.swiggy_user_id = identity["user_id"] or user.swiggy_user_id
    now = datetime.utcnow()
    user.last_login_at = now
    user.updated_at = now
    db.add(user)
    await db.commit()
    await db.refresh(user)

    expires_in = token_response.get("expires_in", TOKEN_TTL)
    expires_at = time.time() + expires_in
    await redis.setex(
        token_redis_key(user.id),
        expires_in,
        json.dumps(
            {
                "access_token": encrypt(access_token),
                "expires_at": expires_at,
                "scope": token_response.get("scope", "mcp:tools"),
            }
        ),
    )

    session_token = await create_session(user.id, user.phone or "")
    return AuthResponse(
        soiree_session=session_token,
        user=UserRead.model_validate(user),
        is_new=is_new,
        swiggy_expires_at=expires_at,
    )


@router.get("/status", summary="Is the Swiggy token still live?")
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


@router.post("/logout", summary="Sign out — revoke the Swiggy token and the session")
async def auth_logout(
    user: User = Depends(current_user),
    x_soiree_session: str | None = Header(None, alias="X-Soiree-Session"),
):
    import httpx

    from app.services.auth.oauth import LOGOUT_URL

    redis = await get_redis()

    token_data_raw = await redis.get(token_redis_key(user.id))
    if token_data_raw:
        access_token = decrypt(json.loads(token_data_raw).get("access_token") or "")
        if access_token:
            try:
                async with httpx.AsyncClient() as client:
                    await client.post(
                        LOGOUT_URL, headers={"Authorization": f"Bearer {access_token}"}
                    )
            except Exception:  # noqa: BLE001 — best-effort revocation
                pass
        await redis.delete(token_redis_key(user.id))

    await revoke_session(x_soiree_session)
    return {"message": "Signed out"}


async def get_access_token(user_id: str) -> str | None:
    """
    The Swiggy MCP Bearer token for a Soirée user, or None if the token has
    expired (→ the user must sign in again).
    """
    if not user_id:
        return None

    redis = await get_redis()
    token_data_raw = await redis.get(token_redis_key(user_id))
    if not token_data_raw:
        return None

    token_data = json.loads(token_data_raw)
    if is_token_expired(token_data.get("expires_at", 0)):
        await redis.delete(token_redis_key(user_id))
        return None

    return decrypt(token_data.get("access_token") or "") or None


async def purge_swiggy_token(user_id: str) -> None:
    """Delete a user's stored Swiggy token — used by account deletion."""
    if not user_id:
        return
    redis = await get_redis()
    await redis.delete(token_redis_key(user_id))
