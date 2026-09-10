"""
scripts/peek_token.py — inspect a stored Swiggy access token.

Answers one question: does Swiggy's OAuth token carry a stable user
identity (customer id / phone) we could log Soirée in with, or is it
opaque? Decides whether Swiggy OAuth can replace phone-OTP as the login.

Reads whatever `swiggy_token:*` entries are in Redis (put there by the
OAuth flow), decrypts each, and — if the token is a JWT — prints its
header + payload claims. Never prints the raw token or its signature.

    cd backend && python ../scripts/peek_token.py

Needs Redis reachable (same REDIS_URL the app uses) and at least one
completed Swiggy OAuth.
"""

import asyncio
import base64
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.core.crypto import decrypt  # noqa: E402
from app.core.redis import get_redis  # noqa: E402


def _b64url_json(segment: str) -> dict:
    pad = segment + "=" * (-len(segment) % 4)
    return json.loads(base64.urlsafe_b64decode(pad))


def _describe(token: str) -> None:
    parts = token.split(".")
    if len(parts) != 3:
        print(f"  opaque token ({len(token)} chars) — not a JWT, no claims to read.")
        print("  → Swiggy OAuth alone can't identify the user; keep phone-OTP.")
        return

    try:
        header = _b64url_json(parts[0])
        payload = _b64url_json(parts[1])
    except Exception as e:  # noqa: BLE001
        print(f"  looks like a JWT but couldn't decode it: {e}")
        return

    print("  JWT header:")
    print("    " + json.dumps(header, indent=2).replace("\n", "\n    "))
    print("  JWT payload claims:")
    print("    " + json.dumps(payload, indent=2, default=str).replace("\n", "\n    "))

    # Highlight anything that could serve as a stable per-user key.
    id_like = [
        k for k in payload
        if k.lower() in {"sub", "uid", "user_id", "userid", "customer_id",
                         "customerid", "phone", "mobile", "msisdn", "email"}
    ]
    print()
    if id_like:
        print(f"  → candidate identity claims: {', '.join(id_like)}")
        print("    If one of these is stable across logins, Swiggy OAuth can")
        print("    replace phone-OTP as the Soirée login.")
    else:
        print("  → no obvious identity claim. Check `sub`/`aud` above by hand;")
        print("    if there's nothing stable, keep phone-OTP.")


async def main() -> None:
    redis = await get_redis()
    keys = sorted(await redis.keys("swiggy_token:*"))
    if not keys:
        print("No swiggy_token:* keys in Redis — complete a Swiggy OAuth first.")
        return

    for key in keys:
        raw = await redis.get(key)
        if not raw:
            continue
        try:
            token = decrypt(json.loads(raw).get("access_token") or "")
        except Exception as e:  # noqa: BLE001
            print(f"{key}: could not read ({e})")
            continue
        print(f"{key}:")
        if not token:
            print("  (empty)")
        else:
            _describe(token)
        print()


if __name__ == "__main__":
    asyncio.run(main())
