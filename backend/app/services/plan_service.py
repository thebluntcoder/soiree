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
from sqlmodel import select
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

    # Store cost breakdown as integers for queryability.
    # Per-service costs are None when that service wasn't part of the plan.
    plan.dineout_cost = parse_cost(parsed.get("dineoutCost", ""))
    plan.food_cost = parse_cost(parsed.get("foodCost", ""))
    plan.instamart_cost = parse_cost(parsed.get("instamartCost", ""))
    plan.total_cost = parse_cost(parsed.get("totalCost", ""))
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
