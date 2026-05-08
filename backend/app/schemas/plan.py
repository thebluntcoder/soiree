"""
schemas/plan.py — Pydantic request/response schemas for plan endpoints.

CONCEPT: Schemas vs Models — what's the difference?
-----------------------------------------------------
Models (models/event.py etc.) define the DATABASE shape — what gets
stored in Postgres. They map 1:1 to tables and columns.

Schemas (this file) define the API shape — what the client sends
and what we send back. They're used for:
  - Validating incoming request bodies
  - Shaping outgoing JSON responses
  - Auto-generating OpenAPI docs (the /docs page)

Often they overlap but they're kept separate because:
  - DB models have fields the API should never expose (internal IDs, timestamps)
  - API schemas have computed fields that don't exist in the DB
  - You might want different validation rules at the API vs DB layer

CONCEPT: Pydantic validation
------------------------------
When FastAPI receives a POST body, it passes it through the Pydantic
schema automatically. If any field fails validation (wrong type, out
of range, missing required field), FastAPI returns a 422 error with
a detailed explanation — before your code even runs.

Field(ge=1) means "greater than or equal to 1".
Field(le=100) means "less than or equal to 100".
These are enforced automatically.
"""

from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum


class EventType(str, Enum):
    """Occasion types — drives AI tone and MCP server selection."""

    date = "date"
    friends = "friends"
    birthday = "birthday"
    corporate = "corporate"
    house_party = "house_party"
    family = "family"


class VenueMode(str, Enum):
    """
    Controls which Swiggy MCP servers are called.
      out    → Dineout only
      home   → Food + Instamart
      hybrid → all three
    """

    out = "out"
    home = "home"
    hybrid = "hybrid"


class Guest(BaseModel):
    """
    A single named guest with optional per-person dietary tags.

    Both fields are optional — if name is None, the guest is anonymous.
    If dietary_tags is empty, group-level tags from PlanRequest apply.
    """

    name: Optional[str] = Field(default=None, description="Guest's name, e.g. 'Anjali'")
    dietary_tags: list[str] = Field(
        default=[],
        description="Per-guest dietary restrictions. E.g. ['Veg', 'No-Nuts']. "
        "These are merged with group-level tags for menu filtering.",
    )


class PlanRequest(BaseModel):
    """
    Incoming request body for POST /api/v1/plans/generate.

    This is the single source of truth for what the frontend
    sends when a user clicks 'Plan My Event'.
    """

    event_type: EventType
    venue_mode: VenueMode

    location: str = Field(
        ...,
        min_length=2,
        description="City name or full address. E.g. 'Koramangala, Bangalore' or 'Lucknow'",
    )
    start_hour: float = Field(
        default=20,
        ge=10,
        le=23,
        description="Event start time in 24h format. 20 = 8 PM, 20.5 = 8:30 PM.",
    )
    budget: int = Field(
        ...,
        ge=500,
        le=50000,
        description="Total budget in INR across all Swiggy services.",
    )
    guest_count: int = Field(
        ...,
        ge=1,
        le=100,
        description="Total number of people including the host.",
    )
    guests: list[Guest] = Field(
        default=[],
        description="Optional named guest list with per-person dietary tags. "
        "If empty, guest_count is used as headcount with group dietary_tags.",
    )
    dietary_tags: list[str] = Field(
        default=[],
        description="Group-level dietary restrictions applied to all guests. "
        "E.g. ['Veg', 'Jain', 'No-Nuts']. "
        "Per-guest tags in `guests` are merged with these.",
    )
    health_focus: int = Field(
        default=50,
        ge=0,
        le=100,
        description="Wellness slider. 0 = full indulgence, 100 = health-first. "
        "Shapes AI dish filtering and recommendations.",
    )
    notes: Optional[str] = Field(
        default=None,
        max_length=500,
        description="Free-text context for the AI. "
        "E.g. 'It is our anniversary' or 'One guest is allergic to nuts'.",
    )
