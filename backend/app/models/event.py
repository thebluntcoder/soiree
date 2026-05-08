"""
models/event.py — Event database model and its enums.

CONCEPT: Enums in SQLModel
---------------------------
Python's Enum class creates a fixed set of allowed values for a field.
SQLModel stores these as strings in Postgres (VARCHAR).
Using enums instead of raw strings gives us:
  - Auto-validation: "house_partyyy" raises an error at the schema layer
  - IDE autocomplete and type safety throughout the codebase
  - Self-documenting code — the allowed values are explicit

CONCEPT: Event vs Plan
------------------------
An Event is the *intent* — what the user wants to organise.
A Plan is the *output* — what the AI generated for that event.
One Event can have multiple Plans (user can regenerate or tweak).
This separation lets us track edit history and compare plans.

CONCEPT: Storing guests as JSON string
---------------------------------------
Guests are a variable-length list of objects: [{name, dietary_tags}].
Rather than a separate `guests` table (which would need a foreign key,
join queries etc.), we store the whole list as a JSON string for Phase 1.
This is a pragmatic tradeoff — simpler to build, slightly harder to query.
We'd normalise this into a proper `event_guests` table in Phase 3
when we build group consensus mode.
"""

from sqlmodel import SQLModel, Field
from typing import Optional
from datetime import datetime
from enum import Enum
import uuid


class EventType(str, Enum):
    """
    The occasion type. Drives the AI planner's tone, budget split logic,
    and which Swiggy MCP servers are prioritised.

    - date: romantic, Dineout-first, smaller group (2 people)
    - friends: casual, hybrid works well, medium group (3-8)
    - birthday: celebratory, dessert-heavy, flexible size
    - corporate: professional tone, GST receipt needed, larger groups
    - house_party: Instamart-heavy (drinks, snacks, ingredients), large group
    - family: dietary complexity high, home preference, all ages
    """

    date = "date"
    friends = "friends"
    birthday = "birthday"
    corporate = "corporate"
    house_party = "house_party"
    family = "family"


class VenueMode(str, Enum):
    """
    Determines which Swiggy MCP servers are called.

    - out:    Dineout only → search_restaurants_dineout + book_table
    - home:   Food + Instamart → search_restaurants + search_products
    - hybrid: All 3 → restaurant first, continue at home (the full arc)

    Hybrid is the core differentiator of Soirée — no other product
    orchestrates the full evening across all three Swiggy services.
    """

    out = "out"
    home = "home"
    hybrid = "hybrid"


class EventStatus(str, Enum):
    """
    Lifecycle of an event from creation to completion.

    draft     → user is still configuring
    planned   → AI has generated a plan, user reviewing
    ordered   → order agent has placed all Swiggy orders
    completed → event happened, can collect feedback
    cancelled → user cancelled before ordering
    """

    draft = "draft"
    planned = "planned"
    ordered = "ordered"
    completed = "completed"
    cancelled = "cancelled"


class Event(SQLModel, table=True):
    """
    Core event record. Created when a user submits the event setup form.

    The foreign key to users.id creates a relationship in Postgres —
    you cannot create an Event for a user_id that doesn't exist in users.
    This is called "referential integrity" and prevents orphaned records.
    """

    __tablename__ = "events"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    user_id: str = Field(
        foreign_key="users.id",
        index=True,
        description="Owner of this event. Index speeds up 'get all events for user' queries.",
    )

    event_type: EventType
    venue_mode: VenueMode
    status: EventStatus = Field(default=EventStatus.draft)

    # Location — stored as text + optional coordinates.
    # Text is what the user typed ("Koramangala, Bangalore").
    # Coordinates are resolved later via geocoding (utils/location.py)
    # and passed to Swiggy MCPs for accurate radius-based search.
    location: str = Field(description="City name or full address as entered by user")
    latitude: Optional[float] = Field(
        default=None, description="Resolved via geocoding"
    )
    longitude: Optional[float] = Field(
        default=None, description="Resolved via geocoding"
    )

    # Timing
    event_date: Optional[datetime] = Field(
        default=None, description="Date of the event, None = today"
    )
    start_hour: float = Field(
        ge=10,
        le=23,
        description="Event start time in 24h format. ge/le = greater/less than or equal (validation).",
    )

    # Budget — total INR for the entire event across all services.
    # The AI planner splits this intelligently: e.g. for a hybrid event,
    # 60% Dineout + 25% Food delivery + 15% Instamart.
    budget: int = Field(
        description="Total budget in INR for all Swiggy services combined"
    )

    # Guests
    guest_count: int = Field(ge=1, le=100)
    guests: Optional[str] = Field(
        default=None,
        description='JSON list of named guests. E.g. \'[{"name": "Anjali", "dietary_tags": ["Veg"]}]\'. '
        "None means headcount-only — group dietary_tags apply to everyone.",
    )

    # Dietary restrictions at group level (apply to all guests unless
    # overridden per-guest in the guests JSON above).
    dietary_tags: Optional[str] = Field(
        default=None,
        description='JSON list. E.g. \'["Veg", "No-Nuts"]\'. '
        "Used to filter menus across all three MCP servers.",
    )

    # health_focus drives how the AI frames recommendations:
    # 0 = full indulgence (biryani, butter chicken, desserts)
    # 100 = health-first (salads, grilled, low-cal, high-protein)
    health_focus: int = Field(
        default=50,
        ge=0,
        le=100,
        description="Wellness slider: 0=indulgent, 100=healthy. Shapes AI dish filtering.",
    )

    notes: Optional[str] = Field(
        default=None,
        description="Free-text context from user. E.g. 'it is our anniversary' or 'client is vegan and allergic to nuts'.",
    )

    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
