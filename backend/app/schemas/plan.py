"""
schemas/plan.py — Pydantic request/response schemas for plan endpoints.

CONCEPT: Schemas vs Models
----------------------------
Models define DB shape. Schemas define API shape.
Kept separate because API and DB concerns differ — validation rules,
exposed fields, and computed properties all differ between layers.

Updated to support:
  - alcohol_preference: drives restaurant and Instamart selection
  - selected_dineout_id: user-chosen restaurant from /plans/search
  - selected_food_restaurant_id: user-chosen food restaurant
  - SearchRequest: lightweight pre-generation restaurant discovery
"""

from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum


class EventType(str, Enum):
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
      home   → Food + Instamart (full meal delivered)
      hybrid → Dineout + Food (celebration items only e.g. cake) + Instamart (ambience)
    """

    out = "out"
    home = "home"
    hybrid = "hybrid"


class AlcoholPreference(str, Enum):
    """
    User's alcohol preference.
    Drives restaurant filtering (bar vs family) and Instamart (beer vs soft drinks).
    """

    yes = "yes"  # alcohol welcome — rooftop bars, wine lists
    no = "no"  # no alcohol — family restaurants, mocktails
    any = "any"  # no preference


class Guest(BaseModel):
    name: Optional[str] = Field(default=None)
    dietary_tags: list[str] = Field(default=[])
    pref: str = Field(default="any", description="any/veg/non-veg")
    allergens: list[str] = Field(
        default=[], description="List of allergens e.g. ['Nuts', 'Dairy']"
    )


class SearchRequest(BaseModel):
    """
    Lightweight pre-generation request — Step 1.5 in the flow.
    Calls MCP servers and returns restaurant options for user to pick.
    No Claude call — just raw restaurant data.

    Flow:
      Step 1: User fills form
      Step 1.5: POST /plans/search → returns restaurant options
      Step 2: User picks restaurants
      Step 3: POST /plans/generate with selected IDs → full plan
    """

    event_type: EventType
    venue_mode: VenueMode
    location: str = Field(..., min_length=2)
    start_hour: float = Field(default=20, ge=10, le=23)
    budget: int = Field(..., ge=100, le=50000)
    guest_count: int = Field(..., ge=1, le=100)
    guests: list[Guest] = Field(default=[])
    dietary_tags: list[str] = Field(default=[])
    health_focus: int = Field(default=50, ge=0, le=100)
    alcohol_preference: AlcoholPreference = Field(
        default=AlcoholPreference.any,
        description="Drives restaurant type and drink suggestions",
    )
    notes: Optional[str] = Field(default=None, max_length=500)
    lat: Optional[float] = Field(default=None)
    lng: Optional[float] = Field(default=None)


class PlanRequest(BaseModel):
    """
    Full plan generation request — Step 3 in the flow.
    Includes user-selected restaurant IDs from Step 2.
    Claude generates a focused plan around the chosen restaurants.
    """

    event_type: EventType
    venue_mode: VenueMode
    location: str = Field(..., min_length=2)
    start_hour: float = Field(default=20, ge=10, le=23)
    budget: int = Field(..., ge=100, le=50000)
    guest_count: int = Field(..., ge=1, le=100)
    guests: list[Guest] = Field(default=[])
    dietary_tags: list[str] = Field(default=[])
    health_focus: int = Field(default=50, ge=0, le=100)
    alcohol_preference: AlcoholPreference = Field(
        default=AlcoholPreference.any,
        description="Drives restaurant type, drink suggestions, Instamart items",
    )
    notes: Optional[str] = Field(default=None, max_length=500)
    lat: Optional[float] = Field(default=None)
    lng: Optional[float] = Field(default=None)

    # User-selected restaurants from the Step 2 picker.
    # The frontend sends the FULL restaurant object (shape = one item of the
    # `dineout` / `food` list returned by POST /search/) so the planner can
    # build the plan around the exact choice without re-resolving an ID
    # against a fresh MCP search (IDs are not guaranteed stable between calls).
    # If None, Claude picks the best option from the MCP data.
    selected_dineout: Optional[dict] = Field(
        default=None,
        description="Full Dineout restaurant object chosen by the user, or null",
    )
    selected_food: Optional[dict] = Field(
        default=None,
        description="Full Food restaurant object chosen by the user, or null",
    )

    # IDs kept for logging / analytics / future server-side resolution.
    selected_dineout_id: Optional[str] = Field(
        default=None,
        description="ID of the chosen Dineout restaurant (mirrors selected_dineout.id)",
    )
    selected_food_restaurant_id: Optional[str] = Field(
        default=None,
        description="ID of the chosen Food restaurant (mirrors selected_food.id)",
    )
