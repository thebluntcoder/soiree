"""
tests_e2e/conftest.py — live-stack E2E fixtures.

WHAT "LIVE STACK" MEANS HERE
-----------------------------
A real FastAPI app, talking to a real (throwaway) Postgres database and a
real Redis, driven by a real browser via Playwright hitting demo.html over
real HTTP. The only thing not real is Claude — see `_mock_claude` below for
why, and why it's safe to fake even though a real browser drives real HTTP
requests into the server.

PRECONDITIONS (not automated — see CLAUDE.md's Commands section)
-----------------------------------------------------------------------------
  1. Postgres + Redis running: `docker compose up -d db redis`
  2. A throwaway `soiree_e2e` database exists and is migrated:
       docker exec soiree-postgres psql -U postgres -c 'CREATE DATABASE soiree_e2e'
       DATABASE_URL=postgresql://postgres:postgres@localhost:5432/soiree_e2e alembic upgrade head
  3. `pip install -r requirements.txt -r requirements-e2e.txt && playwright install chromium`

This suite is intentionally NOT part of the default `pytest -q` (it lives
outside tests/, so pytest.ini's `testpaths = tests` never finds it) — run
it explicitly: `pytest tests_e2e -q`.

HOW THE LIVE SERVER WORKS
---------------------------
uvicorn runs in a background thread inside THIS process (not a subprocess)
so a real browser can hit it over real HTTP, while the app.* modules it
imports are the exact same module objects this conftest imports — so
monkeypatching `planner._get_clients` here really does change what the
live server does when a request comes in, from any thread, because it's
the same interpreter and the same module `__dict__`.
"""

import http.server
import os
import socket
import threading
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

# Must happen before any `app.*` import — Settings() reads these once, at
# import time. A dedicated database/redis-db so this suite never touches
# whatever you're using for local dev.
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/soiree_e2e"
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")
os.environ.setdefault("APP_ENV", "development")

import asyncio  # noqa: E402
import json  # noqa: E402
import secrets  # noqa: E402

import redis.asyncio as aioredis  # noqa: E402
import uvicorn  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlmodel import select  # noqa: E402

from app.main import app  # noqa: E402
from app.models.user import User  # noqa: E402

# Matches services/auth/session.py's key format and TTL exactly — seeding
# deliberately doesn't import that module (see soiree_session's docstring).
_SESSION_KEY = "soiree_session:{token}"
_SESSION_TTL_SECONDS = 30 * 24 * 3600

FRONTEND_PUBLIC_DIR = Path(__file__).resolve().parent.parent.parent / "frontend" / "public"

# A recognisable plan, formatted exactly like the real system prompt asks
# for (section markers parse_plan.py and demo.html's parser both expect).
_PLAN_TEXT = (
    "[BRIEF]\n"
    "A relaxed rooftop date night, dinner then a light dessert delivery.\n"
    "[TIMELINE]\n"
    "8:00 PM | 🍽️ | Dinner at Farzi Cafe | Rooftop table for two\n"
    "9:30 PM | 🍰 | Dessert delivery | Chocolate lava cake from Meghana Foods\n"
    "[DINEOUT]\n"
    "Farzi Cafe — Modern Indian, rooftop seating, 4.6★, known for candlelight ambience.\n"
    "[FOOD]\n"
    "Meghana Foods — Andhra, dessert delivery in about 30 minutes.\n"
    "[INSTAMART]\n"
    "Tealight candles and a small bouquet for the table.\n"
    "[HEALTH]\n"
    "Balanced choices; the dessert portion is kept light.\n"
    "[OFFERS]\n"
    "15% off pre-booking at Farzi Cafe.\n"
    "[COST]\n"
    "Dineout: ₹1,800 | Food Delivery: ₹400 | Instamart: ₹150\n"
    "TOTAL: ₹2,350"
)
_REFINE_REPLY = "Farzi Cafe's rooftop table is the romantic pick here — good call."


@pytest.fixture(scope="session", autouse=True)
def _mock_claude():
    """
    Every plan-generation / refine call in this suite gets canned Claude
    output instead of the real API. There's no cheap way around this: the
    planner always calls the real `messages.create()` (only the Swiggy MCP
    layer has a built-in mock mode), and burning real Anthropic spend +
    non-determinism on every E2E run is a worse trade than faking the one
    LLM call at the boundary this test doesn't actually need to verify —
    it's testing the UI/API wiring around plan generation, not Claude's
    output quality (that's `tests/unit/test_planner.py`'s job).
    """
    from app.services.ai import planner as planner_mod

    class _Content:
        def __init__(self, text):
            self.text = text

    class _Usage:
        input_tokens = 100
        output_tokens = 50

    class _Message:
        def __init__(self, text):
            self.content = [_Content(text)]
            self.usage = _Usage()

    async def _fake_create(*, system, **_kwargs):
        # refine_plan's classify prompt vs generate_plan's concierge prompt
        if "You refine an already-generated event plan" in system:
            import json

            return _Message(json.dumps({"action": "answer", "reply": _REFINE_REPLY, "patch": {}}))
        return _Message(_PLAN_TEXT)

    fake_client = AsyncMock()
    fake_client.messages.create = AsyncMock(side_effect=_fake_create)

    original = planner_mod._get_clients
    planner_mod._get_clients = lambda: (fake_client, original()[1], original()[2])
    yield
    planner_mod._get_clients = original


def _run_async(coro):
    """
    Run a coroutine to completion from a plain sync fixture.

    Can't just `asyncio.run(coro)` here: pytest-asyncio's `auto` mode
    (pytest.ini) leaves an event loop active on the main thread even for
    a purely sync test/fixture, and `asyncio.run()` refuses to nest. A
    throwaway thread has no such loop, so it sidesteps the conflict
    entirely rather than fighting pytest-asyncio's loop management.
    """
    box: dict = {}

    def runner():
        box["result"] = asyncio.run(coro)

    t = threading.Thread(target=runner)
    t.start()
    t.join()
    return box["result"]


# demo.html hardcodes API_BASE = 'http://localhost:8000' whenever
# window.location.hostname === 'localhost' (see demo.html's <script>, near
# the top) — there's no env-based override, so the live server has to
# actually be on 8000 and the static server has to be navigated to as
# "localhost" (not "127.0.0.1", which is a different hostname string even
# though it's the same loopback address) for that branch to trigger.
_BACKEND_PORT = 8000


@pytest.fixture(scope="session")
def live_server(_mock_claude):
    """Run the real FastAPI app in a background thread, on port 8000 —
    fixed, not a random free port, to match demo.html's hardcoded
    API_BASE. Fails fast if something else already owns 8000 (e.g. your
    own `uvicorn --reload`) rather than silently colliding with it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", _BACKEND_PORT))
        except OSError as e:
            raise RuntimeError(
                f"port {_BACKEND_PORT} is already in use — stop whatever's "
                "running there (e.g. your own `uvicorn --reload`) before "
                "running the E2E suite, since demo.html hardcodes this port."
            ) from e

    config = uvicorn.Config(app, host="127.0.0.1", port=_BACKEND_PORT, log_level="warning")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=lambda: asyncio.run(server.serve()), daemon=True)
    thread.start()

    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    if not server.started:
        raise RuntimeError("live_server didn't start within 15s")

    yield f"http://localhost:{_BACKEND_PORT}"

    server.should_exit = True
    thread.join(timeout=10)


# http://localhost:3000 is already in config.py's default ALLOWED_ORIGINS
# (it's the Next.js dev server's usual port) — serving the static frontend
# there means the browser's CORS preflight to the live backend just works,
# with no ALLOWED_ORIGINS override needed (which couldn't happen anyway:
# Settings() reads it once at app.main import time, before any fixture —
# including a random free port for this server — has run).
_STATIC_PORT = 3000


@pytest.fixture(scope="session")
def static_server():
    """Serve frontend/public/ so demo.html loads over http://localhost —
    it must be that exact hostname string (see the API_BASE note above),
    not file:// and not 127.0.0.1."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", _STATIC_PORT))
        except OSError as e:
            raise RuntimeError(
                f"port {_STATIC_PORT} is already in use — stop whatever's "
                "running there (e.g. `npm run dev` in frontend/) before "
                "running the E2E suite: this port is in the backend's "
                "default ALLOWED_ORIGINS, which is why it's used here."
            ) from e

    handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(  # noqa: E731
        *a, directory=str(FRONTEND_PUBLIC_DIR), **kw
    )
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", _STATIC_PORT), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://localhost:{_STATIC_PORT}"
    httpd.shutdown()
    thread.join(timeout=5)


@pytest.fixture
def soiree_session():
    """
    A real logged-in user + session token, seeded directly (no Swiggy
    OAuth possible in an automated browser test).

    Deliberately does NOT import app.core.database / app.core.redis / the
    real create_session() — those are the same module-level singletons the
    live server uses, and each singleton binds to whichever asyncio event
    loop first touches it. `_run_async` gives every fixture its own
    throwaway loop, so reusing the app's singletons here would bind them
    to a loop that's immediately torn down — the live server's own request
    handling would then fail with "attached to a different loop" the
    moment it (also) tried to use them. Fresh, disposable connections
    here sidestep that entirely; the JSON shape and key format below just
    have to match services/auth/session.py's, which they do.
    """

    async def _seed():
        engine = create_async_engine(os.environ["DATABASE_URL"])
        session_factory = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with session_factory() as session:
            result = await session.execute(
                select(User).where(User.swiggy_sub == "e2e-test-sub")
            )
            user = result.scalar_one_or_none()
            if not user:
                user = User(swiggy_sub="e2e-test-sub", name="E2E Test User")
                session.add(user)
                await session.commit()
                await session.refresh(user)
            user_id = user.id
        await engine.dispose()

        redis = aioredis.from_url(os.environ["REDIS_URL"], decode_responses=True)
        token = secrets.token_urlsafe(32)
        await redis.setex(
            _SESSION_KEY.format(token=token),
            _SESSION_TTL_SECONDS,
            json.dumps({"user_id": user_id, "phone": ""}),
        )
        await redis.aclose()
        return token

    token = _run_async(_seed())
    yield token

    async def _cleanup():
        redis = aioredis.from_url(os.environ["REDIS_URL"], decode_responses=True)
        await redis.delete(_SESSION_KEY.format(token=token))
        await redis.aclose()

    _run_async(_cleanup())


@pytest.fixture
def authed_page(context, soiree_session, live_server, static_server):
    """
    A Playwright page already "logged in" — the session token is written to
    localStorage via an init script, which (unlike setting it after
    navigation) runs before demo.html's own startup script reads it.
    """
    import json

    context.add_init_script(
        f"window.localStorage.setItem('soiree_session', {json.dumps(soiree_session)});"
    )
    page = context.new_page()
    yield page, static_server
