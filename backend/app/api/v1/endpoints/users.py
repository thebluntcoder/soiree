"""
api/v1/endpoints/users.py — phone-OTP login for Soirée itself.

Flow:
  1. POST /users/otp/request  {phone}          → sends a 6-digit code
  2. POST /users/otp/verify   {phone, code}    → { soiree_session, user, is_new }
  3. frontend stores `soiree_session` and sends it as X-Soiree-Session
  4. GET  /users/me                            → the current user
  5. POST /users/logout                        → revoke the session

No SMS provider configured (MSG91_AUTH_KEY unset) → the code is logged to
the server, and outside production the magic code `000000` also verifies.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.api.v1.deps import current_user
from app.core.database import get_session
from app.core.ratelimit import rate_limit
from app.models.user import User
from app.schemas.user import AuthResponse, OTPRequest, OTPVerify, UserRead
from app.services.auth.otp import issue_otp, normalize_phone, verify_otp
from app.services.auth.session import create_session, revoke_session

logger = logging.getLogger(__name__)
router = APIRouter()

_OTP_REQUEST_LIMIT = Depends(rate_limit("otp_request", limit=5, window_seconds=600))
_OTP_VERIFY_LIMIT = Depends(rate_limit("otp_verify", limit=15, window_seconds=600))


@router.post(
    "/otp/request", summary="Send a login code to a phone number",
    dependencies=[_OTP_REQUEST_LIMIT],
)
async def otp_request(payload: OTPRequest):
    try:
        phone = normalize_phone(payload.phone)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    try:
        await issue_otp(phone)
    except Exception as e:  # noqa: BLE001
        logger.error("OTP send failed for %s: %s", phone, e)
        raise HTTPException(
            status_code=502, detail="Couldn't send the code. Try again in a moment."
        )
    return {"sent": True, "phone": phone}


@router.post(
    "/otp/verify", response_model=AuthResponse,
    summary="Verify a login code and start a session",
    dependencies=[_OTP_VERIFY_LIMIT],
)
async def otp_verify(payload: OTPVerify, db: AsyncSession = Depends(get_session)):
    try:
        phone = normalize_phone(payload.phone)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    if not await verify_otp(phone, payload.code):
        raise HTTPException(
            status_code=401, detail="That code is wrong or expired. Request a new one."
        )

    result = await db.execute(select(User).where(User.phone == phone))
    user = result.scalar_one_or_none()
    is_new = user is None
    if is_new:
        user = User(phone=phone, name=(payload.name or None))
    elif payload.name and not user.name:
        user.name = payload.name
    user.last_login_at = datetime.utcnow()
    user.updated_at = datetime.utcnow()
    db.add(user)
    await db.commit()
    await db.refresh(user)

    token = await create_session(user.id, phone)
    return AuthResponse(
        soiree_session=token, user=UserRead.model_validate(user), is_new=is_new
    )


@router.get("/me", response_model=UserRead, summary="The current user")
async def get_me(user: User = Depends(current_user)) -> User:
    return user


@router.post("/logout", summary="End the current session")
async def logout(
    x_soiree_session: str | None = Header(None, alias="X-Soiree-Session"),
):
    await revoke_session(x_soiree_session)
    return {"ok": True}
