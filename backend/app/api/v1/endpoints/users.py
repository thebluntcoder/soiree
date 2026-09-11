"""
api/v1/endpoints/users.py — the current user, and deleting them.

Login lives in api/v1/endpoints/auth.py (Swiggy OAuth). This module
exposes the resulting user, a session-only sign-out, and account deletion.

  GET    /users/me      → the current user
  POST   /users/logout   → drop the Soirée session (Swiggy token left to expire)
  DELETE /users/me       → purge everything: events, plans, Swiggy token,
                            session, the user row itself
"""

import logging

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.api.v1.deps import current_user
from app.core.database import get_session
from app.models.event import Event
from app.models.plan import Plan
from app.models.user import User
from app.schemas.user import UserRead
from app.services.auth.session import revoke_session

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/me", response_model=UserRead, summary="The current user")
async def get_me(user: User = Depends(current_user)) -> User:
    return user


@router.post("/logout", summary="Drop the current Soirée session")
async def logout(x_soiree_session: str | None = Header(None, alias="X-Soiree-Session")):
    """Session-only sign-out. Full sign-out (also revokes Swiggy) is POST /auth/logout."""
    await revoke_session(x_soiree_session)
    return {"ok": True}


@router.delete("/me", summary="Delete my account and all my data")
async def delete_me(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
    x_soiree_session: str | None = Header(None, alias="X-Soiree-Session"),
):
    """
    Irreversibly purge everything Soirée holds about this user:
    every plan, every event, the Swiggy token, this session, and the
    account row itself. There is no undo and no confirmation step here —
    the frontend must confirm before calling this.
    """
    # Import locally — auth.py imports users.py's neighbours (deps), and a
    # top-level import here would risk a cycle as the endpoints module grows.
    from app.api.v1.endpoints.auth import purge_swiggy_token

    plans = (
        await session.execute(select(Plan).where(Plan.user_id == user.id))
    ).scalars().all()
    for plan in plans:
        await session.delete(plan)

    events = (
        await session.execute(select(Event).where(Event.user_id == user.id))
    ).scalars().all()
    for event in events:
        await session.delete(event)

    await session.delete(user)
    await session.commit()

    await purge_swiggy_token(user.id)
    await revoke_session(x_soiree_session)

    logger.info(
        "deleted user %s: %d plan(s), %d event(s)", user.id, len(plans), len(events)
    )
    return {
        "deleted": True,
        "plans_deleted": len(plans),
        "events_deleted": len(events),
    }
