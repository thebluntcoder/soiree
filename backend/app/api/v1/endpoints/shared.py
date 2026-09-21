"""
api/v1/endpoints/shared.py — the read-only public view of a shared plan.

This is the ONLY unauthenticated endpoint that reads a user's plan, which
is why it lives in its own file rather than alongside the owner-only plan
endpoints in plans.py.

ACCESS MODEL: capability URL. Possessing the token (128 random bits from
secrets.token_urlsafe(16), set by POST /plans/{id}/share) *is* the
permission — no login, no Swiggy token, so the recipient can be anyone.
The owner revokes with DELETE /plans/{id}/share, after which the token no
longer resolves and this endpoint 404s.

WHAT'S EXPOSED: only the fields on `SharedPlanView` — an explicit
allowlist, built by hand below, never the raw Plan row. Notably NOT
exposed: user/event/plan IDs, booking IDs, order status/errors, the
resolved Dineout slot list, and the event's typed location (which can be
a home address on a house-party plan).

Every response carries `Cache-Control: no-store` so a revoked link can't
keep being served out of a browser or edge cache.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.database import get_session
from app.core.ratelimit import rate_limit
from app.models.event import Event
from app.schemas.plan import SharedPlanView
from app.services.plan_service import get_plan_by_share_token

logger = logging.getLogger(__name__)

router = APIRouter()

# Unauthenticated → the limiter falls back to the client IP. Generous: a
# shared link gets opened, refreshed and re-opened, but nobody legitimately
# needs hundreds an hour, and guessing a 128-bit token is hopeless anyway —
# this is just to stop a scraper hammering the DB.
_VIEW_LIMIT = Depends(rate_limit("shared_view", limit=120, window_seconds=3600))


@router.get(
    "/{token}",
    response_model=SharedPlanView,
    summary="View a shared plan (no login required)",
    dependencies=[_VIEW_LIMIT],
)
async def view_shared_plan(
    token: str,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    response.headers["Cache-Control"] = "no-store"

    plan = await get_plan_by_share_token(session, token)
    if not plan:
        # Same 404 whether the token never existed or was revoked.
        raise HTTPException(status_code=404, detail="This shared plan isn't available.")

    result = await session.execute(select(Event).where(Event.id == plan.event_id))
    event = result.scalar_one_or_none()

    return SharedPlanView(
        event_type=event.event_type if event else None,
        guest_count=event.guest_count if event else None,
        created_at=plan.created_at.isoformat() if plan.created_at else None,
        timeline=plan.timeline,
        dineout_options=plan.dineout_options,
        food_options=plan.food_options,
        instamart_cart=plan.instamart_cart,
        health_insight=plan.health_insight,
        active_offers=plan.active_offers,
        dineout_cost=plan.dineout_cost,
        food_cost=plan.food_cost,
        instamart_cost=plan.instamart_cost,
        total_cost=plan.total_cost,
        total_savings=plan.total_savings,
    )
