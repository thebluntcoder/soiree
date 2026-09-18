"""
services/plan_service.py — Database operations for plans.

CONCEPT: Service layer pattern
--------------------------------
Instead of putting DB logic directly in endpoints, we create a
service layer. This keeps endpoints thin (just HTTP concerns)
and makes DB logic reusable and testable.

Endpoint does: validate request → call service → return response
Service does:  all DB operations, business logic

This also means when we add auth later, the service doesn't change —
only the endpoint changes to pass the real user_id.

OPERATIONS IN THIS FILE:
  create_plan      → insert new Plan record with status=generating
  update_plan_text → update plan content after generation completes
  get_plan         → fetch single plan by ID
  list_plans       → fetch all plans for a user (newest first)
  get_event_plans  → fetch all plans for a specific event
"""

import json
from datetime import datetime
from sqlmodel import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.plan import Plan, PlanStatus


async def create_plan(
    session: AsyncSession,
    event_id: str,
    user_id: str,
) -> Plan:
    """
    Insert a new Plan record with status=generating.

    Called at the START of plan generation — before Claude runs.
    This gives us a record we can update once generation completes.

    Returns the created Plan with its generated UUID.
    """
    plan = Plan(
        event_id=event_id,
        user_id=user_id,
        status=PlanStatus.generating,
    )
    session.add(plan)
    await session.commit()
    await session.refresh(plan)
    return plan


async def update_plan_text(
    session: AsyncSession,
    plan_id: str,
    raw_text: str,
    parsed: dict,
    dineout_selection: dict | None = None,
) -> Plan | None:
    """
    Update plan content after Claude finishes generating.

    Called at the END of plan generation with the full parsed plan.
    Updates status from 'generating' → 'ready'.

    Args:
        plan_id:  UUID of the plan to update
        raw_text: full raw text from Claude (with ⏎ encoded)
        parsed:   dict with keys: brief, dineout, food, instamart,
                  health, offers, cost, totalCost, totalSavings, timeline
        dineout_selection: generate_plan's `resolved["dineout"]` out-param
                  (restaurant_id, name, date, guest_count, start_hour,
                  available_slots) — what order placement later re-derives
                  a real slotId from. None if no Dineout restaurant was
                  actually selected for this plan.

    CONCEPT: Extracting cost integers from strings
    ------------------------------------------------
    Claude returns costs as strings like "₹2,417". We strip the ₹
    and commas then cast to int for DB storage so we can query
    "plans under ₹3000" later.
    """
    result = await session.execute(select(Plan).where(Plan.id == plan_id))
    plan = result.scalar_one_or_none()
    if not plan:
        return None

    def parse_cost(s: str) -> int | None:
        """Extract integer from cost string e.g. '₹2,417' → 2417"""
        try:
            return int(s.replace("₹", "").replace(",", "").strip())
        except (ValueError, AttributeError):
            return None

    # Store timeline as JSON string
    plan.timeline = json.dumps(parsed.get("timeline", []))

    # Store section content
    plan.dineout_options = parsed.get("dineout", "")
    plan.food_options = parsed.get("food", "")
    plan.instamart_cart = parsed.get("instamart", "")
    plan.health_insight = parsed.get("health", "")
    plan.active_offers = parsed.get("offers", "")
    plan.dineout_selection = json.dumps(dineout_selection) if dineout_selection else None

    # Cost breakdown: [COST] is now structured JSON (see parse_plan.py's
    # parse_cost_block), so parse_plan_text() already returns these as
    # int | None — no string parsing needed here. total_savings is the
    # exception: it comes from [OFFERS], which is still free text.
    plan.dineout_cost = parsed.get("dineoutCost")
    plan.food_cost = parsed.get("foodCost")
    plan.instamart_cost = parsed.get("instamartCost")
    plan.total_cost = parsed.get("totalCost")
    plan.total_savings = parse_cost(parsed.get("totalSavings", ""))

    # Mark as ready — user can now see and approve the plan
    plan.status = PlanStatus.ready

    session.add(plan)
    await session.commit()
    await session.refresh(plan)
    return plan


async def get_plan(session: AsyncSession, plan_id: str) -> Plan | None:
    """Fetch a single plan by ID. Returns None if not found."""
    result = await session.execute(select(Plan).where(Plan.id == plan_id))
    return result.scalar_one_or_none()


async def approve_and_claim_for_ordering(
    session: AsyncSession, plan_id: str
) -> tuple[Plan | None, str | None]:
    """
    Atomic ready -> ordering compare-and-swap, stamping approved_at.

    Guards against a double "Confirm & Book" submit (double-click, a
    duplicate BackgroundTasks invocation, two open tabs) with a single
    conditional UPDATE ... WHERE status='ready' — not a select-then-write,
    which would have a race between the read and the write under
    concurrent requests. This is the only idempotency guard book_table
    gets on the app side (see services/orders/dineout_ordering.py's
    module docstring for why no key is sent to Swiggy itself).

    Returns (plan, None) if this call won the race, or (None, reason)
    where reason is the plan's current status (already claimed by another
    request) or "not_found".
    """
    result = await session.execute(
        update(Plan)
        .where(Plan.id == plan_id, Plan.status == PlanStatus.ready)
        .values(status=PlanStatus.ordering, approved_at=datetime.utcnow())
        .returning(Plan.id)
    )
    await session.commit()
    if result.first() is None:
        current = await get_plan(session, plan_id)
        return None, (current.status if current else "not_found")

    plan = await get_plan(session, plan_id)
    return plan, None


async def save_order_result(
    session: AsyncSession,
    plan_id: str,
    *,
    status: PlanStatus,
    dineout_booking_id: str | None = None,
    order_error: str | None = None,
) -> None:
    """
    Terminal write from place_dineout_booking's own DB session (the
    request's session is gone once FastAPI's BackgroundTasks runs, after
    the response is already sent — see workers/tasks.py).
    """
    plan = await get_plan(session, plan_id)
    if not plan:
        return
    plan.status = status
    if dineout_booking_id is not None:
        plan.dineout_booking_id = dineout_booking_id
    plan.order_error = order_error
    session.add(plan)
    await session.commit()


async def list_user_plans(
    session: AsyncSession,
    user_id: str,
    limit: int = 20,
) -> list[Plan]:
    """
    Fetch most recent plans for a user across all their events.
    Ordered newest first. Limited to avoid large payloads.
    """
    result = await session.execute(
        select(Plan)
        .where(Plan.user_id == user_id)
        .where(Plan.status == PlanStatus.ready)
        .order_by(Plan.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_event_plans(
    session: AsyncSession,
    event_id: str,
) -> list[Plan]:
    """
    Fetch all plans generated for a specific event.
    Useful for showing regeneration history.
    """
    result = await session.execute(
        select(Plan).where(Plan.event_id == event_id).order_by(Plan.created_at.desc())
    )
    return list(result.scalars().all())
