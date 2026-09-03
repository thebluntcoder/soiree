"""
api/v1/endpoints/users.py — Current-user endpoint.

Soirée's own auth (phone + OTP via MSG91) is Phase 2. Until then every
request is attributed to a single demo user, created on first use. This
endpoint returns that user so the frontend has something concrete to show
("Signed in as …") and so the shape is settled before real auth lands.

  GET /api/v1/users/me
"""

from fastapi import APIRouter, Depends
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.events import DEMO_USER_ID, _ensure_demo_user
from app.core.database import get_session
from app.models.user import User
from app.schemas.user import UserRead

router = APIRouter()


@router.get("/me", response_model=UserRead, summary="The current (demo) user")
async def get_me(session: AsyncSession = Depends(get_session)) -> User:
    """
    Return the current user.

    Phase 1: always the demo user (created if missing). Phase 2 will read
    the user id off the authenticated session instead.
    """
    await _ensure_demo_user(session)
    result = await session.execute(select(User).where(User.id == DEMO_USER_ID))
    return result.scalar_one()
