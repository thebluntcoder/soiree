# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Soirée — an AI life-events concierge. A user picks an occasion (date, birthday,
house party...), and the backend orchestrates Swiggy's Food/Instamart/Dineout
MCP servers in parallel, then has Claude turn the results into a complete
plan (timeline, restaurant pick, grocery cart, offers, cost breakdown) over
SSE. Two runnable frontends exist; only one is current — see below.

## Commands

### Backend (`backend/`)

```bash
# one-shot local setup (Postgres + Redis via Docker, deps, migrations, seed)
./scripts/setup.sh

# run the API
cd backend && uvicorn app.main:app --reload

# tests
cd backend && pytest -q                                  # everything
cd backend && pytest tests/unit/test_auth.py -q           # one file
cd backend && pytest tests/unit/test_auth.py::TestAuthCallback::test_new_user_gets_session -q  # one test

# E2E (Playwright, live local stack) — NOT part of `pytest -q`, see tests_e2e/conftest.py
cd backend && pip install -r requirements-e2e.txt && playwright install chromium
cd backend && pytest tests_e2e -q

# migrations (Alembic is the only schema authority — app startup does NOT create_all)
cd backend && alembic upgrade head
cd backend && alembic revision --autogenerate -m "description"
cd backend && alembic downgrade -1

# mint a local dev session without doing real Swiggy OAuth (no Swiggy token → mock MCP data)
cd backend && python ../scripts/seed.py

# inspect a stored Swiggy access token's JWT claims (decodes, never verifies signature)
cd backend && python ../scripts/peek_token.py
```

No lint/format config is checked into the repo (no ruff/black/mypy config file) — match the
surrounding code's style rather than reaching for a formatter.

### Frontend

`frontend/public/demo.html` is a single static file, no build step — open it directly or
serve it as-is. It is the **only frontend**. `frontend/src/` is a minimal Next.js app kept
alive purely to host `/auth/callback` and `/callback` — the Swiggy-whitelisted OAuth redirect
URIs (see "Auth" above) — at `https://soiree-blue.vercel.app`; Vercel's build for that
project needs a real app to build, and Swiggy needs a real route to redirect to. Its old
page/components/hooks/lib tree (a second, unauthenticated UI that 401ed on every call) was
deleted — don't add real UI back under `frontend/src/`; that recreates the problem.

```bash
cd frontend && npm run dev     # Next.js dev server (stale app only — demo.html needs no server)
cd frontend && npm run build
```

### CI

`.github/workflows/ci.yml` runs two jobs on every push/PR to `main`: `pytest -q`, and an
Alembic `upgrade head → downgrade base → upgrade head` round-trip against a real Postgres
service container. A migration that only works one direction, or an idempotency guard that
isn't actually idempotent, fails this.

## Architecture

### Auth: Swiggy OAuth *is* the login — there is no separate account

Signing in means completing Swiggy's OAuth 2.1 PKCE flow (`services/auth/oauth.py`,
`api/v1/endpoints/auth.py`). The Swiggy MCP access token that comes back is a JWT; its `sub`
claim (decoded without signature verification — the token comes straight from Swiggy's own
token endpoint, never from a client) is the durable identity, stored as `users.swiggy_sub`.
`GET /auth/start` is public and requires `consent=true` (the privacy-policy checkbox);
`POST /auth/callback` is also public — it exchanges the code, resolves the user from the
JWT, and *creates* a 30-day Soirée session (`X-Soiree-Session` header,
`services/auth/session.py`, opaque Redis token). Every other endpoint requires that header
via the `current_user` dependency (`api/v1/deps.py`).

The Swiggy access token itself is a **separate**, shorter-lived thing: 5 days, no refresh,
stored encrypted in Redis keyed by `swiggy_token:{user_id}` (`core/crypto.py`, Fernet keyed
off `SECRET_KEY`). A user can have a live 30-day session with a lapsed Swiggy token —
`GET /auth/status` reports `connected: false` in that case, and the frontend prompts
"Reconnect Swiggy" (same OAuth flow, not a full re-login).

### Real vs. mock MCP data — decided per call, not by a flag

Every MCP client method takes an `access_token`. If present, `BaseMCPClient._call_mcp`
(`services/mcp/base.py`) makes a real JSON-RPC call to Swiggy; if `None`, it dispatches to
that client's own `_mock_*` methods. There's no `USE_MOCK` setting — a logged-in user with
no Swiggy connection transparently gets mock data of the identical shape. Real MCP responses
are **human-readable text**, not structured JSON — `services/mcp/parse_mcp.py` normalizes
Food's embedded-JSON-in-text and Dineout's numbered-text-line formats into the same shape
the mocks return.

### Two-step plan generation

`POST /search/` fetches real restaurant options (fast, no Claude call) for a picker UI;
the user picks (or doesn't), then `POST /plans/generate` builds the actual plan, optionally
enriching a picked Dineout restaurant via `get_restaurant_details` first
(`services/ai/planner.py::_enrich_dineout`). `MCPOrchestrator.gather_context`
(`services/mcp/orchestrator.py`) resolves a saved Swiggy address from the typed
location/city (with a large synonym/neighbourhood table) before firing Food/Instamart/Dineout
concurrently via `asyncio.gather`.

### Ordering: Dineout only, BackgroundTasks not Celery, undo is pre-send

`POST /plans/{plan_id}/order` books a real Dineout table via `book_table` — the only
service actually wired up (Food/Instamart need a real dish/product picker first; see
TODO.md §4). The resolved restaurant + slot list is captured at generation time into
`Plan.dineout_selection` (`planner.py`'s `generate_plan(..., resolved=...)` out-param,
same pattern as `usage`) since Claude's own prose has no structural link back to one
specific slot. Order placement always re-fetches slots live rather than trusting the
stored ones — `parse_mcp.closest_slot()` picks whichever is nearest the plan's
`start_hour` — matching `dineout.py`'s own "never cache slot data" rule.

`book_table` is NOT idempotent and Swiggy documents no idempotency-key param, so
`services/orders/dineout_ordering.py::book_with_retry` is deliberately conservative: a
4xx gets one corrective retry with a fresh slot; a 5xx/timeout is genuinely ambiguous
and tries to recover a `bookingId` from the failed response before falling back to a
"is the slot still there?" heuristic — if that's inconclusive too, it stops rather than
risk a double booking. `Plan.status` (`ready → ordering` via an atomic
`UPDATE ... WHERE status='ready'` in `plan_service.approve_and_claim_for_ordering`) is
the only guard against *Soirée's own system* double-submitting — nothing is sent to
Swiggy as an idempotency key. See the module docstring in `dineout_ordering.py` for the
full state machine.

Runs via FastAPI `BackgroundTasks`, not Celery, even though `celery==5.6.3` has sat in
`requirements.txt` since early on — the current Railway deploy is a single process with
no worker service, and one bounded MCP call doesn't justify standing that up yet.
Revisit once Food+Instamart also need background execution.

The 60s "undo" window in `demo.html` is pre-send, not post-send: there's no confirmed
`cancel_booking` tool, so the countdown happens *before* `book_table` ever fires, and
undo just means the request is never sent — not cancelling something already booked.

### The SSE stream has two encoding tricks, both load-bearing

1. Claude's plan text contains real newlines, which the SSE framing protocol treats as
   message separators — so `\n` is encoded as `⏎` before sending and decoded back to `\n`
   client-side (`planner.py` / `demo.html`). This is why plan generation collects the full
   Claude response with `.create()` rather than streaming token-by-token — mid-token SSE
   chunk boundaries would arrive without the `data: ` prefix and get dropped.
2. `generate_plan()`'s pipeline (parallel MCP calls, then one non-streaming Claude call) is
   silent for 30-60s. Railway's edge proxy resets an HTTP/2 stream that goes that long
   without bytes, so `plans.py::_with_heartbeat` wraps the generator and injects an SSE
   comment (`: ping\n\n`) on any gap — the frontend parser already ignores non-`data:` lines.

### Analytics is fully opt-in by env var, not by code path

`services/analytics.py` wraps PostHog. With `POSTHOG_API_KEY` unset (the default), every
function is a true no-op — no client constructed, `posthog` package never imported. Every
call site (`auth.py`, `search.py`, `plans.py`) calls `analytics.capture(...)` unconditionally;
the module itself absorbs failures so a PostHog outage can't affect a request. The pre-login
funnel step (`swiggy_auth_started`) uses the OAuth `state` as a temporary distinct_id;
`analytics.alias()` merges it onto the real `user.id` once `/auth/callback` resolves one.

### Alembic migrations are idempotent by inspection, not just additive

The DB predates Alembic (tables were originally created by `SQLModel.metadata.create_all`).
Every migration in `alembic/versions/` inspects the live schema (`sa.inspect(op.get_bind())`)
before adding a column/table/index, so `alembic upgrade head` is safe to run against both a
fresh database and the pre-existing production one. Follow this pattern for new migrations —
don't assume a clean slate. Native Postgres `ENUM` types vs. `VARCHAR` on old DBs is a known,
accepted divergence (documented in the initial migration's docstring).

### Rate limiting and error taxonomy

`core/ratelimit.py` is a Redis fixed-window limiter keyed on `X-Soiree-Session`, falling back
to a legacy Swiggy session header, then client IP; it fails open if Redis is down. MCP auth
failures are normalized to `PermissionError` in `base.py` and mapped to specific frontend
actions in `docs/mcp-integration.md` (401 → re-run OAuth, 419 → full re-auth, 403 → scope
error) — check that doc before changing MCP error handling.

### An unhandled exception's CORS headers come from middleware, not `@app.exception_handler`

`main.py` catches every unhandled exception in a plain `try/except` inside `BaseHTTPMiddleware`
(`_catch_unhandled_exceptions`), registered **before** `CORSMiddleware` — not via
`@app.exception_handler(Exception)`. That's deliberate, not a style choice: Starlette
special-cases a handler registered for the bare `Exception` class (or status `500`) — it's
pulled out of the normal handler dict and passed to `ServerErrorMiddleware`, which Starlette
places *outside every `app.add_middleware()` middleware*, CORS included (see
`Starlette.build_middleware_stack`). A response built by that handler never passes back
through `CORSMiddleware`, so the browser reports a "blocked by CORS policy" error that has
nothing to do with CORS — it's masking whatever the real 500 was. Verified empirically while
fixing a real production bug this way (`tests/unit/test_error_handling.py`). Since
`app.add_middleware()` prepends, the middleware added **last** ends up **outermost** — CORS
has to stay the last one added so it keeps wrapping this catch-all, not the other way round.
Also captures to Sentry (`SENTRY_DSN`, same no-op-when-unset pattern as PostHog below) and
logs the full traceback before returning a generic `{"detail": "..."}` body — never the raw
exception message, which could leak internals.

### Logging is JSON, wired once at the top of `main.py`

`core/logging.py::configure_logging()` routes every `app.*` logger through a JSON formatter
(one object per line: timestamp/level/logger/message, plus anything passed via
`extra={...}`) — call it before adding a new top-level log call site expecting plain text.
It does not touch uvicorn's own access/error logs (separate loggers, `propagate=False`).
Pass `extra={"user_id": ..., "restaurant_id": ...}` etc. on any log call that names a
specific entity so it's filterable later, rather than only interpolating it into the message.

### Tests use hand-written fakes, not fixtures or a real DB/Redis

There's no `aiosqlite`/database fixture and no `fakeredis` dependency. Tests that need
Redis-like behavior build a small dict-backed fake class inline (see `test_auth.py`,
`test_security.py`) and `monkeypatch` the module's `get_redis`. Endpoint functions are called
directly with stub `db`/`session` objects rather than going through FastAPI's test client.
Follow this pattern for new tests rather than introducing a DB fixture.

### E2E tests are the one exception: a genuinely live stack, in `tests_e2e/`

`backend/tests_e2e/` (Playwright, `pip install -r requirements-e2e.txt`) is deliberately outside
this fake-everything convention and outside `pytest -q` (it lives outside `pytest.ini`'s
`testpaths = tests`, so run it explicitly with `pytest tests_e2e -q`). It runs the real FastAPI
app in a background thread against a real (throwaway) Postgres + Redis, driven by a real
Chromium browser through `demo.html` — only Claude is mocked, by monkeypatching
`planner._get_clients` (see `conftest.py`'s `_mock_claude`). Two fixed ports are load-bearing:
the live server binds **8000** because `demo.html` hardcodes `API_BASE` to it with no env
override, and the static file server binds **3000** because that's the only origin in the
default `ALLOWED_ORIGINS` — both fixtures fail fast with a clear error if something else (e.g.
your own `uvicorn --reload`) already holds the port. The session-seeding fixture uses its own
throwaway `create_async_engine`/`aioredis.from_url` connections rather than the app's
`AsyncSessionLocal`/`get_redis()` singletons — those bind to whichever asyncio event loop first
touches them, and reusing them from a fixture's own throwaway loop poisons them for the live
server's subsequent requests. See `conftest.py`'s module docstring for full preconditions.

## graphify

This project has a graphify knowledge graph at graphify-out/.

Rules:
- Before answering architecture or codebase questions, read graphify-out/GRAPH_REPORT.md for god nodes and community structure
- If graphify-out/wiki/index.md exists, navigate it instead of reading raw files
- After modifying code files in this session, run `$(cat graphify-out/.graphify_python) -c "from graphify.watch import _rebuild_code; from pathlib import Path; _rebuild_code(Path('.'))"` to keep the graph current — bare `python3` doesn't have the `graphify` package installed on this machine; `graphify-out/.graphify_python` records the interpreter that does
