"""
scripts/seed.py — Seed a local dev login.

Soirée has no demo user any more — every request needs a real logged-in
user (phone-OTP). For local work you don't want to wire up SMS, so this
script:

  1. creates (or reuses) a dev user with phone +91 99999 99999
  2. mints a Soirée session for them in Redis
  3. prints the session token and how to use it

Then either:
  - paste the token into the browser:  localStorage.soiree_session = "<token>"
  - or just log in through the UI with phone 9999999999 and OTP 000000
    (the magic code works whenever APP_ENV != production)

    cd backend && python ../scripts/seed.py

Idempotent — safe to re-run (you get a fresh session each time).
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

DEV_PHONE = "+919999999999"


async def seed() -> None:
    async with AsyncSessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.phone == DEV_PHONE))
        ).scalar_one_or_none()
        if not user:
            user = User(phone=DEV_PHONE, name="Dev User", default_city="Lucknow")
            session.add(user)
            await session.commit()
            await session.refresh(user)
            print(f"created dev user {user.id} ({DEV_PHONE})")
        else:
            print(f"dev user {user.id} ({DEV_PHONE}) already exists")

        token = await create_session(user.id, DEV_PHONE)

    await engine.dispose()

    print()
    print("Soirée session (valid 30 days):")
    print(f"  {token}")
    print()
    print("Use it in the browser console on the demo page:")
    print(f'  localStorage.soiree_session = "{token}"; location.reload()')
    print()
    print("Or log in via the UI: phone 9999999999, OTP 000000")


if __name__ == "__main__":
    asyncio.run(seed())
