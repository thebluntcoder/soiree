"""
schemas/user.py — Request/response shapes for user endpoints.

Phone-OTP auth for Soirée itself is Phase 2 — until then every request is
attributed to the demo user (see api/v1/endpoints/users.py). This schema
is the read shape returned by GET /users/me.

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
    phone: str
    name: Optional[str] = None
    email: Optional[str] = None
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
