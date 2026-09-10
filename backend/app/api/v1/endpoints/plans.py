"""
api/v1/endpoints/plans.py — Plan generation, persistence and retrieval.

Every endpoint requires a logged-in Soirée user (`Depends(current_user)`).
The user's Swiggy MCP token, if they have connected one, is looked up by
`user.id`; without it the planner falls back to mock data.

CONCEPT: How persistence works with streaming
----------------------------------------------
Streaming and DB persistence seem to conflict — streaming sends data
immediately while DB writes happen after. We solve this by:

  1. Create a Plan record (status=generating) BEFORE streaming starts
     → gives us a plan_id immediately
  2. Stream Claude's response to the frontend chunk by chunk
  3. Accumulate the full text server-side while streaming
  4. After stream completes, parse + save to DB (status=ready)

The frontend receives:
  - First chunk: "data: PLAN_ID:<uuid>\n\n"
  - Subsequent chunks: plan text tokens
  - Final chunk: "data: [DONE]\n\n"

ENDPOINTS:
  POST /plans/generate         → stream plan + persist to DB
  POST /plans/chat             → follow-up chat (streaming, advisory)
  POST /plans/refine           → follow-up: answer OR change the plan
  GET  /plans/history          → recent plans for the current user
  GET  /plans/event/{event_id} → all plans for one of the user's events
  GET  /plans/{plan_id}        → fetch a saved plan the user owns
  POST /plans/{plan_id}/order  → place orders (Phase 2)
"""

import json as _json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.api.v1.deps import current_user
from app.api.v1.endpoints.auth import get_access_token
from app.core.database import get_session
from app.core.ratelimit import rate_limit
from app.lib.parse_plan import parse_plan_text
from app.models.event import Event
from app.models.user import User
from app.schemas.plan import PlanRequest
from app.services.ai.planner import generate_followup, generate_plan, refine_plan
from app.services.plan_service import (
    create_plan,
    get_event_plans,
    get_plan,
    list_user_plans,
    update_plan_text,
)

# Each plan generation / refine is 1-2 Claude calls — cap per caller.
_GENERATE_LIMIT = Depends(rate_limit("plan_generate", limit=25, window_seconds=3600))
_REFINE_LIMIT = Depends(rate_limit("plan_refine", limit=40, window_seconds=3600))

router = APIRouter()


@router.post(
    "/generate",
    summary="Generate an event plan (streaming + persistent)",
    dependencies=[_GENERATE_LIMIT],
)
async def create_plan_endpoint(
    request: PlanRequest,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Generate a plan, stream it to the frontend, and persist it to DB.

    Uses the current user's Swiggy MCP token if they have connected one;
    otherwise the planner runs on mock data.
    """
    access_token = await get_access_token(user.id)

    # One Event per generation call — it captures the exact config the user
    # submitted (occasion, budget, guests, notes …) so the plan's event_id
    # points at real intent, not a frozen first-ever event.
    event = Event(
        user_id=user.id,
        event_type=request.event_type,
        venue_mode=request.venue_mode,
        location=request.location,
        latitude=request.lat,
        longitude=request.lng,
        start_hour=request.start_hour,
        budget=request.budget,
        guest_count=request.guest_count,
        guests=_json.dumps([g.model_dump() for g in request.guests])
        if request.guests
        else None,
        dietary_tags=_json.dumps(request.dietary_tags) if request.dietary_tags else None,
        health_focus=request.health_focus,
        notes=request.notes,
    )
    session.add(event)
    await session.commit()
    await session.refresh(event)

    plan_db = await create_plan(
        session=session,
        event_id=event.id,
        user_id=user.id,
    )

    async def event_stream():
        yield f"data: PLAN_ID:{plan_db.id}\n\n"

        accumulated = ""
        try:
            event_data = request.model_dump()
            event_data["access_token"] = access_token  # None = mock, token = live
            async for chunk in generate_plan(event_data):
                accumulated += chunk
                yield chunk

            parsed = parse_plan_text(accumulated)
            await update_plan_text(
                session=session,
                plan_id=plan_db.id,
                raw_text=accumulated,
                parsed=parsed,
            )
        except Exception as e:  # noqa: BLE001
            yield f"data: [ERROR] {str(e)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            # CORS headers are added by CORSMiddleware (main.py) based on the
            # request Origin — never hard-code "*" here.
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


class ChatRequest(BaseModel):
    user_message: str
    event_data: dict = {}
    conversation_history: list[dict] = []
    # The generated plan text (newlines decoded) — grounds the reply so
    # "switch to Italian" is understood as cuisine, not a language request.
    plan_text: str = ""


@router.post(
    "/chat",
    summary="Follow-up chat (streaming, advisory only)",
    dependencies=[_REFINE_LIMIT],
)
async def chat_followup(
    request: ChatRequest, user: User = Depends(current_user)
):
    """
    Streaming advisory reply — never changes the plan. Kept for the legacy
    Next.js client; demo.html uses POST /plans/refine instead.
    """

    async def event_stream():
        async for chunk in generate_followup(
            user_message=request.user_message,
            conversation_history=request.conversation_history,
            event_data=request.event_data,
            plan_text=request.plan_text,
        ):
            yield chunk

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post(
    "/refine",
    summary="Follow-up: answer a question OR change the plan",
    dependencies=[_REFINE_LIMIT],
)
async def refine(request: ChatRequest, user: User = Depends(current_user)):
    """
    Classify a follow-up message and act on it.

    Returns JSON:
      { "action": "answer", "reply": "<specific answer>", "patch": {} }
      { "action": "modify", "reply": "<what's changing>",
        "patch": { <PlanRequest field overrides> } }

    On "modify" the frontend merges `patch` into the stored request and
    re-runs POST /plans/generate. `patch` is already sanitised server-side.
    """
    return await refine_plan(
        user_message=request.user_message,
        conversation_history=request.conversation_history,
        event_data=request.event_data,
        plan_text=request.plan_text,
    )


@router.get("/event/{event_id}", summary="All plans for one of your events")
async def get_plans_for_event(
    event_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """All plans generated for a specific event you own, newest first."""
    result = await session.execute(
        select(Event).where(Event.id == event_id, Event.user_id == user.id)
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")
    return await get_event_plans(session=session, event_id=event_id)


@router.get("/history", summary="Recent plans for the current user")
async def get_plan_history(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """The 20 most recent ready plans for the current user."""
    return await list_user_plans(session=session, user_id=user.id)


@router.get("/{plan_id}", summary="Fetch a saved plan you own")
async def get_plan_endpoint(
    plan_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    plan = await get_plan(session=session, plan_id=plan_id)
    if not plan or plan.user_id != user.id:
        raise HTTPException(status_code=404, detail=f"Plan {plan_id} not found")
    return plan


@router.post("/{plan_id}/order", summary="Place all orders for an approved plan")
async def place_order(plan_id: str, user: User = Depends(current_user)):
    """
    Agentic ordering — Phase 2 feature.
    Will call: book_table (Dineout) + place_food_order (Food) + checkout (Instamart)
    """
    raise HTTPException(status_code=501, detail="Agentic ordering coming in Phase 2")
