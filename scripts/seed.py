"""
scripts/seed.py — Seed local demo data.

Creates the demo user (demo-user-001) and one sample event so the app has
something to show before the first plan is generated. Idempotent — running
it again is a no-op.

    cd backend && python ../scripts/seed.py
"""

import asyncio
import sys
from pathlib import Path

# Allow running from the repo root or from backend/
BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlmodel import select  # noqa: E402

from app.core.database import AsyncSessionLocal, engine  # noqa: E402
from app.models.event import Event, EventType, VenueMode  # noqa: E402
from app.models.user import User  # noqa: E402

DEMO_USER_ID = "demo-user-001"


async def seed() -> None:
    async with AsyncSessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.id == DEMO_USER_ID))
        ).scalar_one_or_none()
        if not user:
            session.add(
                User(
                    id=DEMO_USER_ID,
                    phone="+919999999999",
                    name="Demo User",
                    default_city="Lucknow",
                )
            )
            await session.commit()
            print(f"created user {DEMO_USER_ID}")
        else:
            print(f"user {DEMO_USER_ID} already exists")

        has_event = (
            await session.execute(
                select(Event).where(Event.user_id == DEMO_USER_ID).limit(1)
            )
        ).scalar_one_or_none()
        if not has_event:
            session.add(
                Event(
                    user_id=DEMO_USER_ID,
                    event_type=EventType.date,
                    venue_mode=VenueMode.hybrid,
                    location="Lucknow",
                    start_hour=20,
                    budget=3000,
                    guest_count=2,
                )
            )
            await session.commit()
            print("created sample event")
        else:
            print("sample event already exists")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
