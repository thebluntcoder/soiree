"""
api/v1/endpoints/orders.py — Order status.

Placing orders (Dineout book_table, Food place_food_order, Instamart
checkout) is Phase 2 and lives at POST /plans/{plan_id}/order.

This router is read-only: it reports whatever order / booking identifiers
have been recorded on a plan so far. Today those are always empty; once
the ordering agent exists it will populate them and this endpoint becomes
the tracking surface.

  GET /api/v1/orders/{plan_id}
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.services.plan_service import get_plan

router = APIRouter()


@router.get("/{plan_id}", summary="Order / booking status for a plan")
async def get_order_status(
    plan_id: str,
    session: AsyncSession = Depends(get_session),
):
    """
    Report the ordering state of a plan.

    `placed` is True once every service that is part of the plan has an
    identifier. Until the Phase 2 ordering agent runs, this is always the
    "not yet ordered" shape.
    """
    plan = await get_plan(session=session, plan_id=plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail=f"Plan {plan_id} not found")

    orders = {
        "dineout": plan.dineout_booking_id,
        "food": plan.food_order_id,
        "instamart": plan.instamart_order_id,
    }
    expected = {
        "dineout": plan.dineout_cost is not None,
        "food": plan.food_cost is not None,
        "instamart": plan.instamart_cost is not None,
    }
    placed = all(
        orders[svc] is not None for svc, needed in expected.items() if needed
    ) and any(expected.values())

    return {
        "plan_id": plan.id,
        "status": plan.status,
        "placed": placed,
        "orders": orders,
        "approved_at": plan.approved_at,
    }
