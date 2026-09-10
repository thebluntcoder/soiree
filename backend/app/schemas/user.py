"""
schemas/user.py — Request/response shapes for user endpoints.

Login is Swiggy OAuth (see api/v1/endpoints/auth.py):
  POST /auth/callback → AuthResponse
  GET  /users/me      → UserRead

The JSON-string preference fields on the User model (preferred_cuisines,
dietary_tags) are deserialised to real lists here.
"""

import json
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, field_validator


class UserRead(BaseModel):
    """Public view of a user."""

    id: str
    phone: Optional[str] = None
    name: Optional[str] = None
    email: Optional[str] = None
    swiggy_user_id: Optional[str] = None
    preferred_cuisines: list[str] = []
    dietary_tags: list[str] = []
    default_city: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    is_active: bool

    class Config:
        from_attributes = True

    @field_validator("preferred_cuisines", "dietary_tags", mode="before")
    @classmethod
    def _load_json_list(cls, v):
        """DB stores these as a JSON string (or None) — turn them into lists."""
        if v is None or v == "":
            return []
        if isinstance(v, str):
            try:
                parsed = json.loads(v)
                return parsed if isinstance(parsed, list) else []
            except json.JSONDecodeError:
                return []
        return v


class AuthResponse(BaseModel):
    """Returned on successful Swiggy sign-in."""

    soiree_session: str
    user: UserRead
    is_new: bool
    swiggy_expires_at: float
