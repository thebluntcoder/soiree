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
serve it as-is. It is the **only actively developed frontend**; `frontend/src/` is a Next.js
app that predates the current auth model and 401s on every API call (kept for reference,
not wired up). Don't add features there unless explicitly asked to revive it.

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

### Tests use hand-written fakes, not fixtures or a real DB/Redis

There's no `aiosqlite`/database fixture and no `fakeredis` dependency. Tests that need
Redis-like behavior build a small dict-backed fake class inline (see `test_auth.py`,
`test_security.py`) and `monkeypatch` the module's `get_redis`. Endpoint functions are called
directly with stub `db`/`session` objects rather than going through FastAPI's test client.
Follow this pattern for new tests rather than introducing a DB fixture.
