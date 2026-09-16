"""
models/plan.py — Plan database model.

CONCEPT: What a Plan stores
-----------------------------
A Plan is the AI's output for a given Event. It stores:
  1. Raw MCP data — what Swiggy returned for restaurants, products, slots
  2. AI-structured content — the timeline, health insight, cost breakdown
  3. Applied offers — which discounts were active at generation time
  4. Order confirmations — booking IDs after the agent places orders (Phase 2)

CONCEPT: Why store MCP data in the Plan?
-----------------------------------------
Once a plan is generated, we don't want to re-fetch from Swiggy every time
the user views it. We store the snapshot. This also means if a restaurant
gets delisted or a slot fills up between generation and ordering, we can
detect the mismatch at checkout (compare stored plan vs live re-fetch).

CONCEPT: PlanStatus as a state machine
----------------------------------------
The status field tracks the plan through a strict lifecycle:

  generating → ready → approved → ordering → confirmed
                                            ↘ failed

Each transition is triggered by a specific action:
  - generating: set when AI streaming starts
  - ready: set when streaming completes successfully
  - approved: set when user clicks "Confirm Plan"
  - ordering: set when order agent begins placing Swiggy orders
  - confirmed: set when all Swiggy order IDs are received
  - failed: set if any Swiggy order call fails (with error detail)

This state machine pattern is important for the agentic ordering in Phase 2 —
it lets us resume, retry, or rollback cleanly.

As of the first ordering slice (Dineout `book_table` only — Food/Instamart
still have no real item-picker, see TODO.md §4), `ready → approved →
ordering` collapses into one atomic write (`plan_service.
approve_and_claim_for_ordering`) since the UI has a single "Approve &
Order" button rather than a separate approve-then-order step.
"confirmed" currently means "every service requested in *that* order
call succeeded" — for a hybrid plan with Food/Instamart deselected, that's
Dineout alone, not literally "every service the AI originally planned."
"""

from sqlmodel import SQLModel, Field
from typing import Optional
from datetime import datetime
from enum import Enum
import uuid


class PlanStatus(str, Enum):
    """
    Lifecycle states of a generated plan.
    See module docstring for the full state machine explanation.
    """

    generating = "generating"  # Claude is streaming the plan right now
    ready = "ready"  # Plan fully generated, shown to user
    approved = "approved"  # User reviewed and approved — ready to order
    ordering = "ordering"  # Order agent is calling Swiggy APIs
    confirmed = "confirmed"  # All orders placed, booking IDs received
    failed = "failed"  # Something went wrong during ordering


class Plan(SQLModel, table=True):
    """
    Stores the AI-generated plan for an event, including MCP data snapshots,
    cost breakdown, applied offers, and order confirmations.

    Relationship to Event: one Event → many Plans (user can regenerate).
    The most recent Plan with status=ready is shown to the user.
    """

    __tablename__ = "plans"

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), primary_key=True)
    event_id: str = Field(
        foreign_key="events.id",
        index=True,
        description="The event this plan was generated for.",
    )
    user_id: str = Field(
        foreign_key="users.id",
        index=True,
        description="Duplicated here (denormalised) for fast 'all plans by user' queries without joining events.",
    )

    status: PlanStatus = Field(default=PlanStatus.generating)

    # --- AI-generated content (all stored as JSON strings) ---

    timeline: Optional[str] = Field(
        default=None,
        description='JSON list of timeline steps. E.g. \'[{"time": "7:30 PM", "icon": "🍽", "title": "Arrive at restaurant", "detail": "..."}]\'',
    )
    dineout_options: Optional[str] = Field(
        default=None,
        description="JSON snapshot of Dineout MCP search results. Top 3 restaurant options with slot availability.",
    )
    food_options: Optional[str] = Field(
        default=None,
        description="JSON snapshot of Food MCP search results. Top 3 restaurant + dish recommendations.",
    )
    instamart_cart: Optional[str] = Field(
        default=None,
        description="JSON list of Instamart products recommended for the event. Includes product_id for direct add-to-cart.",
    )
    active_offers: Optional[str] = Field(
        default=None,
        description="JSON list of Swiggy offers active at plan generation time. Stored for display + re-validation at checkout.",
    )
    health_insight: Optional[str] = Field(
        default=None,
        description="AI-generated 1-2 sentence note on how the plan aligns with the group's dietary needs and health_focus.",
    )
    dineout_selection: Optional[str] = Field(
        default=None,
        description=(
            "JSON snapshot of the resolved Dineout booking target, captured at "
            "generation time: {restaurant_id, name, date, guest_count, "
            "start_hour, available_slots}. The [DINEOUT] prose has no "
            "structural link back to one specific slot, so this is what "
            "order placement re-derives a slotId from (via closest_slot() "
            "against a freshly re-fetched slot list — never this stale one)."
        ),
    )

    # --- Cost breakdown (in INR) ---
    # Stored as separate fields so we can query "plans under ₹2000" etc.
    # None means that service wasn't used (e.g. no Dineout for home-mode events).

    dineout_cost: Optional[int] = Field(
        default=None, description="Estimated cost for Dineout reservation (INR)"
    )
    food_cost: Optional[int] = Field(
        default=None, description="Estimated Food delivery cost (INR)"
    )
    instamart_cost: Optional[int] = Field(
        default=None, description="Estimated Instamart cart total (INR)"
    )
    total_cost: Optional[int] = Field(
        default=None, description="Sum of all service costs (INR)"
    )
    total_savings: Optional[int] = Field(
        default=None,
        description="Total amount saved via applied offers (INR). Shown prominently in UI.",
    )

    # --- Order confirmations (populated in Phase 2 by the order agent) ---

    dineout_booking_id: Optional[str] = Field(
        default=None,
        description="Booking reference from Dineout MCP book_table call.",
    )
    food_order_id: Optional[str] = Field(
        default=None,
        description="Order ID from Food MCP place_food_order call.",
    )
    instamart_order_id: Optional[str] = Field(
        default=None,
        description="Order ID from Instamart MCP checkout call.",
    )
    order_error: Optional[str] = Field(
        default=None,
        description=(
            "Short machine-readable failure code set when status=failed "
            "(DINEOUT_BOOKING_AMBIGUOUS, SLOT_UNAVAILABLE, RETRY_EXHAUSTED, "
            "SWIGGY_TOKEN_EXPIRED, INTERNAL_ERROR). Surfaced by GET /orders/{plan_id}."
        ),
    )

    # --- Edit tracking ---
    # Every time a user tweaks the plan via chat, edit_count increments.
    # Useful for UX analytics: do users edit a lot? Which parts most?

    edit_count: int = Field(
        default=0, description="How many times user edited this plan via chat"
    )
    last_edited_at: Optional[datetime] = Field(default=None)

    created_at: datetime = Field(default_factory=datetime.utcnow)
    approved_at: Optional[datetime] = Field(
        default=None,
        description="Timestamp when user hit 'Confirm Plan'. Used to measure time-to-order.",
    )
