"""
api/v1/endpoints/plans.py — Plan generation, persistence and retrieval.

CONCEPT: How persistence works with streaming
----------------------------------------------
Streaming and DB persistence seem to conflict — streaming sends data
immediately while DB writes happen after. We solve this by:

  1. Create a Plan record (status=generating) BEFORE streaming starts
     → gives us a plan_id immediately
  2. Stream Claude's response to the frontend chunk by chunk
     → user sees plan being written in real time
  3. Accumulate the full text server-side while streaming
     → we build the complete text in memory
  4. After stream completes, parse + save to DB (status=ready)
     → plan is now persisted

The frontend receives:
  - First chunk: "data: PLAN_ID:<uuid>\n\n" — so it knows the plan ID
  - Subsequent chunks: plan text tokens
  - Final chunk: "data: [DONE]\n\n"

This way the frontend can show the plan ID and link to it
even before the plan is fully saved.

ENDPOINTS:
  POST /plans/generate         → stream plan + persist to DB
  POST /plans/chat             → follow-up chat (streaming)
  GET  /plans/{plan_id}        → fetch saved plan
  GET  /plans/event/{event_id} → all plans for an event
  POST /plans/{plan_id}/order  → place orders (Phase 2)
"""

from fastapi import APIRouter, HTTPException, Depends, Header
from typing import Optional
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel


from app.core.database import get_session
from app.schemas.plan import PlanRequest
from app.services.ai.planner import generate_plan, generate_followup
from app.services.plan_service import (
    create_plan,
    update_plan_text,
    get_plan,
    get_event_plans,
    list_user_plans,
)
from app.lib.parse_plan import parse_plan_text

# Temporary demo user until auth is built in Phase 2
DEMO_USER_ID = "demo-user-001"

router = APIRouter()


@router.post("/generate", summary="Generate an event plan (streaming + persistent)")
async def create_plan_endpoint(
    request: PlanRequest,
    session: AsyncSession = Depends(get_session),
    x_session_id: Optional[str] = Header(None, alias="X-Session-ID"),
):
    """
    Generate a plan, stream it to the frontend, and persist it to DB.

    Returns SSE stream. First message is the plan_id so the frontend
    can reference it. Subsequent messages are plan text. Final message
    is [DONE].

    The event_id in PlanRequest is optional for now — if not provided,
    the plan is saved without an event link (demo mode).
    """
    # Resolve access token if session provided
    # When session_id is present → real Swiggy MCP calls
    # When absent → mock data (demo mode)
    access_token = None
    if x_session_id:
        from app.api.v1.endpoints.auth import get_access_token
        access_token = await get_access_token(x_session_id)
        if access_token is None:
            raise HTTPException(
                status_code=401,
                detail={
                    "code": "SWIGGY_TOKEN_EXPIRED",
                    "message": "Please reconnect your Swiggy account",
                }
            )

    # Ensure the demo user exists, then persist THIS request as a fresh Event.
    # One Event per generation call — it captures the exact config the user
    # submitted (occasion, budget, guests, notes …) so the plan's event_id
    # points at real intent, not a frozen first-ever event.
    import json as _json

    from app.api.v1.endpoints.events import _ensure_demo_user
    from app.models.event import Event

    await _ensure_demo_user(session)

    event = Event(
        user_id=DEMO_USER_ID,
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
        user_id=DEMO_USER_ID,
    )

    async def event_stream():
        # Send plan_id as first message so frontend knows the ID
        yield f"data: PLAN_ID:{plan_db.id}\n\n"

        accumulated = ""
        try:
            event_data = request.model_dump()
            event_data["access_token"] = access_token  # None = mock, token = live
            async for chunk in generate_plan(event_data):
                accumulated += chunk
                yield chunk

            # Stream complete — parse and save to DB
            parsed = parse_plan_text(accumulated)
            await update_plan_text(
                session=session,
                plan_id=plan_db.id,
                raw_text=accumulated,
                parsed=parsed,
            )

        except Exception as e:
            yield f"data: [ERROR] {str(e)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            # CORS headers are added by CORSMiddleware (main.py) based on the
            # request Origin — never hard-code "*" here, it is invalid together
            # with allow_credentials and masks real misconfiguration.
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


class ChatRequest(BaseModel):
    user_message: str
    event_data: dict = {}
    conversation_history: list[dict] = []


@router.post("/chat", summary="Follow-up chat on an existing plan (streaming)")
async def chat_followup(request: ChatRequest):
    """
    Handle follow-up messages after a plan is generated.
    E.g. "make it more romantic", "switch to Italian cuisine".
    """

    async def event_stream():
        async for chunk in generate_followup(
            user_message=request.user_message,
            conversation_history=request.conversation_history,
            event_data=request.event_data,
        ):
            yield chunk

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/event/{event_id}", summary="All plans for an event")
async def get_plans_for_event(
    event_id: str,
    session: AsyncSession = Depends(get_session),
):
    """
    Fetch all plans generated for a specific event.
    Returns newest first — useful for showing regeneration history.
    """
    plans = await get_event_plans(session=session, event_id=event_id)
    return plans


@router.get("/history", summary="Recent plans for the current user")
async def get_plan_history(
    session: AsyncSession = Depends(get_session),
):
    """Fetch the 20 most recent ready plans for the demo user."""
    plans = await list_user_plans(session=session, user_id=DEMO_USER_ID)
    return plans


@router.get("/{plan_id}", summary="Fetch a saved plan by ID")
async def get_plan_endpoint(
    plan_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Retrieve a previously generated and saved plan."""
    plan = await get_plan(session=session, plan_id=plan_id)
    if not plan:
        raise HTTPException(status_code=404, detail=f"Plan {plan_id} not found")
    return plan


@router.post("/{plan_id}/order", summary="Place all orders for an approved plan")
async def place_order(plan_id: str):
    """
    Agentic ordering — Phase 2 feature.
    Will call: book_table (Dineout) + place_food_order (Food) + checkout (Instamart)
    """
    raise HTTPException(status_code=501, detail="Agentic ordering coming in Phase 2")
