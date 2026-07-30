"""
services/auth/oauth.py — Swiggy OAuth 2.1 with PKCE implementation.

CONCEPT: OAuth 2.1 with PKCE
------------------------------
PKCE (Proof Key for Code Exchange) prevents authorization code interception.
The client generates a random code_verifier, hashes it to code_challenge,
sends the challenge with the auth request, and the verifier with the token
exchange. Only the original client can complete the flow.

SWIGGY-SPECIFIC NOTES (from docs):
  - Dynamic Client Registration (RFC 7591) — no static client_id
    MCP SDK registers the client automatically at /auth/register
  - Phone + OTP consent UI — user sees Swiggy's login page
  - Access token: 5 days (432000 seconds)
  - No refresh tokens in v1 — re-run full OAuth on expiry
  - Scopes: mcp:tools (covers all tool calls on all 3 servers)
  - On 401: re-run authorization flow (treat as expired)
  - On 419: session revoked — full re-auth required (phone + OTP again)

TOKEN STORAGE:
  - Stored in Redis with key: "swiggy_token:{session_id}"
  - TTL: 5 days (matching token expiry)
  - Proactive refresh check: re-auth if expires_at <= now + 60s
  - Never log tokens to disk in plaintext

ENDPOINTS:
  GET  /api/v1/auth/start    → generate PKCE, return Swiggy authorize URL
  GET  /api/v1/auth/callback → exchange code for token, store in Redis
  GET  /api/v1/auth/status   → check if session has valid token
  POST /api/v1/auth/logout   → revoke token, clear Redis
"""

import hashlib
import secrets
import base64
import time
from typing import Optional
from app.core.config import settings


# Swiggy OAuth endpoints
SWIGGY_AUTH_BASE = "https://mcp.swiggy.com"
AUTHORIZE_URL = f"{SWIGGY_AUTH_BASE}/auth/authorize"
TOKEN_URL = f"{SWIGGY_AUTH_BASE}/auth/token"
LOGOUT_URL = f"{SWIGGY_AUTH_BASE}/auth/logout"
REGISTER_URL = f"{SWIGGY_AUTH_BASE}/auth/register"

# Our production redirect URI — must exactly match what's registered with Swiggy
REDIRECT_URI = "https://soiree-blue.vercel.app/auth/callback"

# OAuth scopes — mcp:tools covers all tool calls on all 3 servers
SCOPES = "mcp:tools"

# Redis key prefix for token storage
TOKEN_KEY_PREFIX = "swiggy_token:"
PKCE_KEY_PREFIX = "swiggy_pkce:"


def generate_pkce() -> tuple[str, str]:
    """
    Generate PKCE code_verifier and code_challenge pair.

    code_verifier: cryptographically random 32-byte base64url string
    code_challenge: SHA-256 hash of verifier, base64url encoded (S256 method)

    Returns:
        (code_verifier, code_challenge) tuple
        Store verifier server-side, send challenge to Swiggy
    """
    # 32 random bytes → base64url encoded (no padding)
    code_verifier = secrets.token_urlsafe(32)

    # SHA-256 hash → base64url encoded (no padding)
    digest = hashlib.sha256(code_verifier.encode()).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()

    return code_verifier, code_challenge


def build_authorize_url(
    code_challenge: str,
    state: str,
    client_id: str,
) -> str:
    """
    Build the Swiggy /auth/authorize URL to redirect the user to.

    The user lands on Swiggy's consent UI (phone + OTP login).
    After authenticating, Swiggy redirects to our REDIRECT_URI with ?code=...&state=...

    Args:
        code_challenge: SHA-256 hash of code_verifier (S256 method)
        state: random CSRF token to verify the callback isn't forged
        client_id: from Dynamic Client Registration

    Returns:
        Full authorize URL to redirect user to
    """
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "state": state,
        "scope": SCOPES,
    }
    query = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{AUTHORIZE_URL}?{query}"


async def register_client() -> dict:
    """
    Dynamic Client Registration (RFC 7591).

    Swiggy MCP uses DCR — no static client_id to apply for.
    We register once per deployment and cache the client_id.

    Returns:
        dict with client_id and other registration details
    """
    import httpx

    async with httpx.AsyncClient() as client:
        response = await client.post(
            REGISTER_URL,
            json={
                "client_name": "Soirée",
                "redirect_uris": [REDIRECT_URI],
                "grant_types": ["authorization_code"],
                "response_types": ["code"],
                "scope": SCOPES,
                "token_endpoint_auth_method": "none",  # PKCE public client
            },
        )
        response.raise_for_status()
        return response.json()


async def exchange_code_for_token(
    code: str,
    code_verifier: str,
) -> dict:
    """
    Exchange authorization code for access token.

    Called from the /auth/callback endpoint after Swiggy redirects back.
    This is step 3 in the OAuth flow — verifier proves we initiated the flow.

    Args:
        code: authorization code from Swiggy callback (?code=...)
        code_verifier: original random string we generated in /auth/start

    Returns:
        dict with access_token, expires_in, scope
        Token is valid for 5 days (432000 seconds)

    Raises:
        httpx.HTTPStatusError if exchange fails
    """
    import httpx

    async with httpx.AsyncClient() as client:
        response = await client.post(
            TOKEN_URL,
            json={
                "grant_type": "authorization_code",
                "code": code,
                "code_verifier": code_verifier,
                "redirect_uri": REDIRECT_URI,
            },
        )
        response.raise_for_status()
        return response.json()


def token_redis_key(session_id: str) -> str:
    """Redis key for storing a user's access token."""
    return f"{TOKEN_KEY_PREFIX}{session_id}"


def pkce_redis_key(state: str) -> str:
    """Redis key for storing PKCE verifier during OAuth flow."""
    return f"{PKCE_KEY_PREFIX}{state}"


def is_token_expired(expires_at: float, buffer_seconds: int = 60) -> bool:
    """
    Check if a token is expired or about to expire.
    Re-auth proactively if token expires within buffer_seconds (default 60s).

    Args:
        expires_at: Unix timestamp when token expires
        buffer_seconds: re-auth this many seconds before actual expiry

    Returns:
        True if token is expired or expiring soon
    """
    return time.time() >= (expires_at - buffer_seconds)
