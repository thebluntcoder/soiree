"""
models/user.py — User database model.

CONCEPT: SQLModel table models
--------------------------------
A class with `table=True` maps directly to a Postgres table.
Each class attribute becomes a column. SQLModel handles:
  - Creating the table (via init_db / Alembic)
  - Inserting rows (session.add(user))
  - Querying rows (select(User).where(User.phone == phone))

CONCEPT: Why store preferences as JSON strings?
-----------------------------------------------
Postgres has a native JSONB column type, but SQLModel's support for it
requires extra setup. For simplicity in Phase 1, we store lists (cuisines,
dietary tags) as JSON strings — e.g. '["Veg", "Jain"]'.
We deserialise them in the schema layer (schemas/user.py) using json.loads().
In a later iteration we'd migrate these to proper JSONB columns via Alembic.

CONCEPT: UUIDs as primary keys
--------------------------------
We use UUID strings instead of auto-incrementing integers (1, 2, 3...).
Reasons:
  - UUIDs are globally unique — safe to generate in the app without
    checking the DB first (no race conditions)
  - Integers leak information (user #3 means you have ~3 users)
  - Easier to merge data from multiple sources / environments
"""

from sqlmodel import SQLModel, Field
from typing import Optional
from datetime import datetime
import uuid


class User(SQLModel, table=True):
    """
    Represents a Soirée user.

    Authentication is phone-based (OTP via MSG91) — no passwords.
    This is standard for Indian consumer apps (Swiggy, Zepto, CRED all do this).
    Email is optional, collected later for receipts/notifications.

    Preferences are accumulated over time as the user creates more events —
    the AI planner reads these to personalise plans without the user
    re-entering context every time.
    """

    __tablename__ = "users"

    id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        primary_key=True,
        description="UUID primary key, generated in Python not Postgres",
    )
    phone: str = Field(
        unique=True,
        index=True,
        description="Indian mobile number, used for OTP auth. E.g. +919876543210",
    )
    name: Optional[str] = Field(
        default=None, description="Display name, set after first login"
    )
    email: Optional[str] = Field(
        default=None, description="Optional, for receipts and notifications"
    )

    # Preferences learned from past events — used to personalise future plans.
    # Stored as JSON strings, deserialised in the schema layer.
    preferred_cuisines: Optional[str] = Field(
        default=None,
        description='JSON list of cuisine preferences. E.g. \'["North Indian", "Italian"]\'',
    )
    dietary_tags: Optional[str] = Field(
        default=None,
        description='JSON list of personal dietary restrictions. E.g. \'["Veg", "No-Nuts"]\'',
    )
    default_city: Optional[str] = Field(
        default=None,
        description="User's home city for faster location pre-fill. E.g. 'Lucknow'",
    )

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    last_login_at: Optional[datetime] = Field(
        default=None, description="Set on each successful phone-OTP verify"
    )
    is_active: bool = Field(
        default=True, description="Soft delete flag — False means account deactivated"
    )
