"""
api/v1/deps.py — shared FastAPI dependencies.

`current_user` is the gate: every endpoint that touches a user's data
depends on it, so there is no anonymous access and no `demo-user-001`.
The frontend sends the Soirée session token as `X-Soiree-Session`.
"""

from fastapi import Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.database import get_session
from app.models.user import User
from app.services.auth.session import resolve_session


async def current_user(
    x_soiree_session: str | None = Header(None, alias="X-Soiree-Session"),
    db: AsyncSession = Depends(get_session),
) -> User:
    """The logged-in user, or 401. Use as `user: User = Depends(current_user)`."""
    data = await resolve_session(x_soiree_session)
    if not data:
        raise HTTPException(
            status_code=401,
            detail={"code": "NOT_LOGGED_IN", "message": "Log in with your phone number."},
        )
    result = await db.execute(select(User).where(User.id == data["user_id"]))
    user = result.scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=401,
            detail={"code": "NOT_LOGGED_IN", "message": "Session no longer valid."},
        )
    return user


async def current_user_id(user: User = Depends(current_user)) -> str:
    return user.id
