"""
services/auth/otp.py — phone OTP generation, storage and delivery.

CONCEPT: pluggable sender
-------------------------
The OTP *flow* (generate → store in Redis → verify) is provider-agnostic.
Only delivery differs, so it lives behind `OTPSender`:

  MSG91Sender   — real SMS, used when MSG91_AUTH_KEY is configured
  ConsoleSender — logs the code (local dev); in non-prod also accepts the
                  magic code `000000` so tests / demos need no SMS

Codes are 6 digits, valid `OTP_TTL_SECONDS`, max `OTP_MAX_ATTEMPTS` verify
tries, and requests to the same number are throttled (see the endpoint).
"""

import logging
import secrets
from typing import Protocol

import httpx

from app.core.config import settings
from app.core.redis import get_redis

logger = logging.getLogger(__name__)

OTP_TTL_SECONDS = 300  # 5 minutes
OTP_MAX_ATTEMPTS = 5
DEV_MAGIC_CODE = "000000"

_OTP_KEY = "otp:{phone}"
_ATTEMPTS_KEY = "otp_attempts:{phone}"


def normalize_phone(raw: str) -> str:
    """
    Normalise an Indian mobile number to +91XXXXXXXXXX.
    Accepts '9876543210', '09876543210', '+91 98765 43210', etc.
    Raises ValueError if it doesn't look like one.
    """
    digits = "".join(c for c in raw if c.isdigit())
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    elif digits.startswith("0") and len(digits) == 11:
        digits = digits[1:]
    if len(digits) != 10 or digits[0] not in "6789":
        raise ValueError("Enter a valid 10-digit Indian mobile number.")
    return f"+91{digits}"


class OTPSender(Protocol):
    async def send(self, phone: str, code: str) -> None: ...


class ConsoleSender:
    """Dev sender — logs the code. Never used when MSG91 is configured."""

    async def send(self, phone: str, code: str) -> None:
        logger.warning("OTP for %s → %s  (dev sender)", phone, code)


class MSG91Sender:
    """Sends OTP SMS via MSG91's flow API."""

    URL = "https://control.msg91.com/api/v5/flow/"

    async def send(self, phone: str, code: str) -> None:
        payload = {
            "template_id": settings.MSG91_TEMPLATE_ID,
            "recipients": [
                {"mobiles": phone.lstrip("+"), "OTP": code, "otp": code}
            ],
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                self.URL,
                json=payload,
                headers={"authkey": settings.MSG91_AUTH_KEY},
            )
            resp.raise_for_status()
            body = resp.json()
            if str(body.get("type", "success")).lower() == "error":
                raise RuntimeError(f"MSG91: {body.get('message')}")


def get_otp_sender() -> OTPSender:
    if settings.MSG91_AUTH_KEY and settings.MSG91_TEMPLATE_ID:
        return MSG91Sender()
    return ConsoleSender()


def _is_prod() -> bool:
    return settings.APP_ENV == "production"


async def issue_otp(phone: str) -> None:
    """Generate a code, store it, and send it. Overwrites any pending code."""
    code = f"{secrets.randbelow(1_000_000):06d}"
    redis = await get_redis()
    await redis.setex(_OTP_KEY.format(phone=phone), OTP_TTL_SECONDS, code)
    await redis.delete(_ATTEMPTS_KEY.format(phone=phone))
    await get_otp_sender().send(phone, code)


async def verify_otp(phone: str, code: str) -> bool:
    """
    True if `code` matches the pending OTP for `phone`. Consumes the OTP on
    success; counts failures and locks out after OTP_MAX_ATTEMPTS.
    """
    code = "".join(c for c in code if c.isdigit())
    if not _is_prod() and code == DEV_MAGIC_CODE:
        return True

    redis = await get_redis()
    otp_key = _OTP_KEY.format(phone=phone)
    attempts_key = _ATTEMPTS_KEY.format(phone=phone)

    attempts = int(await redis.get(attempts_key) or 0)
    if attempts >= OTP_MAX_ATTEMPTS:
        return False

    stored = await redis.get(otp_key)
    if stored and secrets.compare_digest(str(stored), code):
        await redis.delete(otp_key, attempts_key)
        return True

    n = await redis.incr(attempts_key)
    if n == 1:
        await redis.expire(attempts_key, OTP_TTL_SECONDS)
    return False
