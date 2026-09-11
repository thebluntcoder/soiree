"""
scripts/seed.py — mint a local dev session.

Login is Swiggy OAuth, but for local UI work you don't always want to run
the real flow. This creates (or reuses) a dev `User` and mints a 30-day
Soirée session for it — no Swiggy token, so MCP calls fall back to mock
data, which is exactly what you want offline.

    cd backend && python ../scripts/seed.py

Paste the printed line into the browser console on the demo page. Re-run
any time for a fresh session.
"""

import asyncio
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlmodel import select  # noqa: E402

from app.core.database import AsyncSessionLocal, engine  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.auth.session import create_session  # noqa: E402

DEV_SWIGGY_SUB = "dev-local-0000"


async def seed() -> None:
    async with AsyncSessionLocal() as session:
        user = (
            await session.execute(
                select(User).where(User.swiggy_sub == DEV_SWIGGY_SUB)
            )
        ).scalar_one_or_none()
        if not user:
            user = User(
                swiggy_sub=DEV_SWIGGY_SUB, name="Dev User", default_city="Lucknow"
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            print(f"created dev user {user.id}")
        else:
            print(f"dev user {user.id} already exists")

        token = await create_session(user.id, "")

    await engine.dispose()

    print()
    print("Soirée session (valid 30 days) — paste into the demo page console:")
    print(f'  localStorage.soiree_session = "{token}"; location.reload()')


if __name__ == "__main__":
    asyncio.run(seed())
