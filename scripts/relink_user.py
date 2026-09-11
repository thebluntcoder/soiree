"""
scripts/relink_user.py — one-off: move an old account's data to the new
Swiggy-identified one.

Before the Swiggy-OAuth-only login, users were keyed by phone. After it,
your first Swiggy sign-in creates a fresh `User` keyed by `swiggy_sub`,
and the old phone-keyed row (with its events + plans) is orphaned.

Run this once, after signing in through Swiggy at least once:

    cd backend && python ../scripts/relink_user.py

It finds the newest user that HAS a swiggy_sub (the new one) and the
newest that does NOT (the old one), reassigns the old user's events and
plans to the new user, copies over name / preferences if the new row is
blank, and deletes the old row. Prints what it will do and asks first.
"""

import asyncio
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlmodel import select  # noqa: E402

from app.core.database import AsyncSessionLocal, engine  # noqa: E402
from app.models.event import Event  # noqa: E402
from app.models.plan import Plan  # noqa: E402
from app.models.user import User  # noqa: E402


async def relink() -> None:
    async with AsyncSessionLocal() as db:
        users = list(
            (await db.execute(select(User).order_by(User.created_at))).scalars()
        )
        new_user = next((u for u in reversed(users) if u.swiggy_sub), None)
        old_user = next((u for u in reversed(users) if not u.swiggy_sub), None)

        if not new_user:
            print("No Swiggy-identified user yet — sign in through Swiggy first.")
            return
        if not old_user:
            print("No orphaned pre-Swiggy user — nothing to relink.")
            return
        if old_user.id == new_user.id:
            print("Only one user; nothing to do.")
            return

        events = list(
            (await db.execute(select(Event).where(Event.user_id == old_user.id))).scalars()
        )
        plans = list(
            (await db.execute(select(Plan).where(Plan.user_id == old_user.id))).scalars()
        )
        print(f"old user {old_user.id}  (phone={old_user.phone!r}, name={old_user.name!r})")
        print(f"new user {new_user.id}  (swiggy_sub={new_user.swiggy_sub})")
        print(f"→ move {len(events)} event(s) and {len(plans)} plan(s), then delete the old row")
        if input("proceed? [y/N] ").strip().lower() != "y":
            print("aborted.")
            return

        for e in events:
            e.user_id = new_user.id
            db.add(e)
        for p in plans:
            p.user_id = new_user.id
            db.add(p)
        new_user.name = new_user.name or old_user.name
        new_user.email = new_user.email or old_user.email
        new_user.phone = new_user.phone or old_user.phone
        new_user.preferred_cuisines = (
            new_user.preferred_cuisines or old_user.preferred_cuisines
        )
        new_user.dietary_tags = new_user.dietary_tags or old_user.dietary_tags
        new_user.default_city = new_user.default_city or old_user.default_city
        db.add(new_user)
        await db.delete(old_user)
        await db.commit()
        print("done.")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(relink())
