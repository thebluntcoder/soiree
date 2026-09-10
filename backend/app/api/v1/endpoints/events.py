"""
api/v1/endpoints/events.py — Event CRUD, scoped to the logged-in user.

Every endpoint depends on `current_user` (401 if not logged in) and only
ever touches rows owned by that user — no cross-user access.

ENDPOINTS:
  POST   /events          → create, returns EventRead
  GET    /events          → the user's events, newest first
  GET    /events/{id}     → one event (404 if not the user's)
  PATCH  /events/{id}     → partial update
  DELETE /events/{id}     → hard delete
"""

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.api.v1.deps import current_user
from app.core.database import get_session
from app.models.event import Event
from app.models.user import User
from app.schemas.event import EventCreate, EventRead, EventUpdate

router = APIRouter()


async def _owned_event(event_id: str, user: User, session: AsyncSession) -> Event:
    result = await session.execute(
        select(Event).where(Event.id == event_id, Event.user_id == user.id)
    )
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")
    return event


@router.post("/", response_model=EventRead, status_code=201)
async def create_event(
    payload: EventCreate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """Create an event owned by the current user."""
    event = Event(
        user_id=user.id,
        event_type=payload.event_type,
        venue_mode=payload.venue_mode,
        location=payload.location,
        start_hour=payload.start_hour,
        budget=payload.budget,
        guest_count=payload.guest_count,
        guests=json.dumps([g.model_dump() for g in payload.guests])
        if payload.guests
        else None,
        dietary_tags=json.dumps(payload.dietary_tags) if payload.dietary_tags else None,
        health_focus=payload.health_focus,
        notes=payload.notes,
    )
    session.add(event)
    await session.commit()
    await session.refresh(event)
    return event


@router.get("/", response_model=list[EventRead])
async def list_events(
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """The current user's events, newest first."""
    result = await session.execute(
        select(Event)
        .where(Event.user_id == user.id)
        .order_by(Event.created_at.desc())
    )
    return result.scalars().all()


@router.get("/{event_id}", response_model=EventRead)
async def get_event(
    event_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    return await _owned_event(event_id, user, session)


@router.patch("/{event_id}", response_model=EventRead)
async def update_event(
    event_id: str,
    payload: EventUpdate,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    """Partial update (only fields present in the payload)."""
    event = await _owned_event(event_id, user, session)

    for field, value in payload.model_dump(exclude_none=True).items():
        if field == "guests" and isinstance(value, list):
            value = json.dumps(
                [g.model_dump() if hasattr(g, "model_dump") else g for g in value]
            )
        elif field == "dietary_tags" and isinstance(value, list):
            value = json.dumps(value)
        setattr(event, field, value)

    event.updated_at = datetime.utcnow()
    session.add(event)
    await session.commit()
    await session.refresh(event)
    return event


@router.delete("/{event_id}", status_code=204)
async def delete_event(
    event_id: str,
    user: User = Depends(current_user),
    session: AsyncSession = Depends(get_session),
):
    event = await _owned_event(event_id, user, session)
    await session.delete(event)
    await session.commit()
