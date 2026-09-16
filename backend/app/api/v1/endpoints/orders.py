"""
api/v1/endpoints/orders.py — Order status.

Placing orders lives at POST /plans/{plan_id}/order. Only Dineout
(book_table) is wired up — Food place_food_order / Instamart checkout
need a real item/dish/product picker first (see TODO.md §4).

This router is read-only: it reports whatever order / booking identifiers
have been recorded on a plan so far, populated by
workers/tasks.py::place_dineout_booking via FastAPI BackgroundTasks. The
frontend polls this after POST /order to learn the outcome.

  GET /api/v1/orders/{plan_id}
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import current_user
from app.core.database import get_session
from app.models.user import User
from app.services.plan_service import get_plan

router = APIRouter()


@router.get("/{plan_id}", summary="Order / booking status for a plan")
async def get_order_status(
    plan_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Report the ordering state of a plan.

    `placed` is True once every service that is part of the plan has an
    identifier. Until the Phase 2 ordering agent runs, this is always the
    "not yet ordered" shape.
    """
    plan = await get_plan(session=session, plan_id=plan_id)
    if not plan or plan.user_id != user.id:
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
        "order_error": plan.order_error,
    }
