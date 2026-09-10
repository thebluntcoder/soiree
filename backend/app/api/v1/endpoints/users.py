"""
api/v1/endpoints/users.py — the current user.

Login lives in api/v1/endpoints/auth.py (Swiggy OAuth). This module just
exposes the resulting user and a session-only sign-out.

  GET  /users/me      → the current user
  POST /users/logout  → drop the Soirée session (Swiggy token left to expire)
"""

from fastapi import APIRouter, Depends, Header

from app.api.v1.deps import current_user
from app.models.user import User
from app.schemas.user import UserRead
from app.services.auth.session import revoke_session

router = APIRouter()


@router.get("/me", response_model=UserRead, summary="The current user")
async def get_me(user: User = Depends(current_user)) -> User:
    return user


@router.post("/logout", summary="Drop the current Soirée session")
async def logout(x_soiree_session: str | None = Header(None, alias="X-Soiree-Session")):
    """Session-only sign-out. Full sign-out (also revokes Swiggy) is POST /auth/logout."""
    await revoke_session(x_soiree_session)
    return {"ok": True}
