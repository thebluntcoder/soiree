"""
schemas/user.py — Request/response shapes for user endpoints.

Soirée login is phone-OTP (see api/v1/endpoints/users.py):
  OTPRequest  → POST /users/otp/request
  OTPVerify   → POST /users/otp/verify → AuthResponse
  UserRead    → GET  /users/me

The JSON-string preference fields on the User model (preferred_cuisines,
dietary_tags) are deserialised to real lists here.
"""

import json
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class OTPRequest(BaseModel):
    """POST /users/otp/request"""

    phone: str = Field(..., min_length=6, max_length=20)


class OTPVerify(BaseModel):
    """POST /users/otp/verify"""

    phone: str = Field(..., min_length=6, max_length=20)
    code: str = Field(..., min_length=4, max_length=8)
    name: Optional[str] = Field(default=None, max_length=80)


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


class AuthResponse(BaseModel):
    """Returned on successful OTP verify."""

    soiree_session: str
    user: UserRead
    is_new: bool
