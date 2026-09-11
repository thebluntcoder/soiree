# Changelog

All notable changes to Soirée will be documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

### Analytics + Anthropic cost tracking (TODO §3)

- **`services/analytics.py`** — thin PostHog wrapper, entirely behind
  `POSTHOG_API_KEY`. Unset (the default) means no client is ever
  constructed and no network call is ever made; every public function
  also swallows its own errors so a PostHog outage can never affect a
  real request. `analytics.shutdown()` flushes queued events on app
  shutdown (wired into `main.py`'s lifespan).
- **Funnel events** — `swiggy_auth_started` (`/auth/start`, keyed to the
  OAuth `state` since there's no user yet) → `swiggy_auth_completed` /
  `swiggy_auth_failed` (`/auth/callback`, with `analytics.alias()` merging
  the anonymous `state` onto the real `user.id`), plus `search`
  (`/search/`), `plan_generated` (`/plans/generate`), `chat_message` and
  `plan_refined` (`/plans/refine`). Properties are counters and flags —
  `event_type`, `venue_mode`, `city`, `budget`, `guest_count`,
  `had_swiggy_token`, `latency_ms` — never the notes/dietary text/chat
  content the user typed. A refine's changed field *names* are sent
  (e.g. `["budget", "notes"]`), never the new values.
- **Anthropic token usage** — `generate_plan()` and `refine_plan()`
  (`services/ai/planner.py`) take an optional `usage` out-param (a
  generator can't return a value, so the caller passes a dict in and
  reads it after `async for` completes) populated from
  `message.usage.{input,output}_tokens`. Attached to `plan_generated` /
  `chat_message` as `tokens_in`/`tokens_out` — left as raw counts rather
  than converted to a $ figure, since per-token pricing changes over time
  and is better computed downstream against current rates.
- `requirements.txt` — `posthog==7.51.2` + its new transitive deps
  (`backoff`, `requests`, `charset-normalizer`, `urllib3`).

### Legal / privacy (TODO §3)

- **Privacy policy** — `frontend/public/privacy.html`, plainly marked as a
  draft written by the dev team (not a lawyer) that needs review before
  Soirée has real users. Explains the Swiggy-OAuth sign-in, what's
  collected and why, where it's stored, the two third parties (Swiggy,
  Anthropic), retention and how to delete it.
- **Consent screen** — the sign-in modal requires a checked "I agree to
  the Privacy Policy" box before the "Sign in with Swiggy" button enables;
  `GET /auth/start` also rejects server-side without `consent=true`, so
  the frontend check isn't the only guard. The timestamp travels through
  the PKCE record and lands on `users.consent_accepted_at` at
  `/auth/callback` (Alembic `c3d4e5f6a7b8`). Reconnecting a lapsed Swiggy
  token pre-checks the box instead of asking a returning user to re-agree.
- **`DELETE /users/me`** — irreversibly purges every plan and event owned
  by the user, the Swiggy token, the current session, and the user row
  itself; returns counts of what was deleted. `demo.html` gets a small
  "Delete my data" link (double-confirmed) next to the account chip.

### Auth — Swiggy OAuth is the login, `demo-user-001` removed (breaking, TODO §3)

- **Every plan / event / search / chat / order endpoint now requires a
  logged-in user.** The hardcoded `demo-user-001` and its
  `_ensure_demo_user` bootstrap are gone. No `X-Soiree-Session` →
  `401 {"code": "NOT_LOGGED_IN"}`.
- **Login *is* Swiggy OAuth.** `GET /auth/start` (public) → user authorises
  on Swiggy's page → `POST /auth/callback {code, state}` exchanges the code,
  reads the MCP access token (a JWT), keys a Soirée `User` off its `sub`
  claim (`swiggy_sub`, `swiggy_user_id` columns), and returns
  `{ soiree_session, user, is_new, swiggy_expires_at }`. No separate
  account, no phone-OTP, no SMS provider, no TRAI DLT.
  `decode_token_identity` reads the claims without verifying the signature
  (the token came straight from Swiggy's token endpoint over TLS); an
  opaque token → `502` and login fails loudly.
- **Sessions** are opaque 30-day Redis tokens (`soiree_session:{token}`),
  sent as `X-Soiree-Session`. `GET /users/me`, `POST /users/logout`
  (session only), `POST /auth/logout` (also revokes the Swiggy token).
- **Swiggy token** stays keyed by `user.id` (`swiggy_token:{user_id}`,
  encrypted, 5-day). `GET /auth/status` → `{ connected, expires_at }`; when
  it lapses the 30-day session survives and the UI shows "Reconnect Swiggy"
  (same OAuth).
- **Ownership checks** unchanged — `GET /plans/{id}`, `/plans/event/{id}`,
  `/events/{id}` (+ PATCH/DELETE), `/orders/{id}` 404 on another user's row.
- Alembic `b2c3d4e5f6a7` — `users.swiggy_sub` (unique) + `swiggy_user_id`,
  `users.phone` NOT NULL → nullable. Idempotent, batch-mode for SQLite.
- **`demo.html`** — the login modal is now one "Sign in with Swiggy"
  button; the header chip reads "Sign in" / the user's name; a stale Swiggy
  token surfaces an amber "Reconnect Swiggy" chip.
- **`scripts/`** — `seed.py` mints a dev session against a fake local user
  (no Swiggy); `peek_token.py` dumps a stored token's JWT claims;
  `relink_user.py` moves a pre-OAuth account's events + plans onto the new
  Swiggy-identified row.
- Removed: `services/auth/otp.py`, `/users/otp/*`, `MSG91_*` +
  `DEV_LOGIN_PHONES` settings — the brief phone-OTP iteration is gone.
- The stale Next.js app (`frontend/src/`) still 401s on every API call —
  only the shared `/auth/callback` route is wired.

### Security (TODO §3)

- **Rate limiter now keys on the Soirée session first** (`X-Soiree-Session`),
  then the legacy Swiggy session, then client IP.
- **Swiggy access tokens are encrypted at rest.** They sat in Redis as
  plaintext JSON for 5 days — anyone with the Redis URL had every
  connected user's food-delivery account. Now Fernet-encrypted with a key
  derived from `SECRET_KEY` (`core/crypto.py`); `decrypt()` passes
  pre-existing plaintext through, so live sessions keep working.
- **`SECRET_KEY` guard** — the app refuses to boot in
  `APP_ENV=production` while it's still the default string.
- **Rate limiting** (`core/ratelimit.py`, Redis fixed-window, per Swiggy
  session / client IP): 25 `/plans/generate`, 40 `/plans/refine` +
  `/plans/chat`, 90 `/search/` per hour — the plan endpoints are 1-2
  Claude calls each. Fails open if Redis is down.
- `/docs`, `/redoc`, `/openapi.json` are disabled in production.

### Fixed
- **The restaurant picker now works with a real Swiggy token (TODO §2).**
  `search.py` expected JSON from every MCP tool, but the real responses
  don't come that way — so a real token gave an empty picker and the
  two-step flow silently skipped to plan generation, where Claude picked
  from a raw blob. New `services/mcp/parse_mcp.py` normalises both real
  shapes (checked against live `_debug` output) into the mock's
  `{"data": {"restaurants": […]}}`:
  - **Food** — a JSON object inside the text field (rich: cuisines, cost,
    distance, delivery ETA, offer, image, veg). Picker cards now show the
    image, a veg dot and the delivery range.
  - **Dineout** — numbered text lines, sparse (name + rating + locality),
    plus a "Search coordinates" line (kept for `get_restaurant_details`).
    0★ / unrated entries dropped, "(Ad)" stripped.
  Options are ranked (rating → Swiggy order → distance) and capped at 8;
  cards render only the fields that exist. Dineout selection rules in the
  system prompt rewritten to lean on rating + locality + occasion and made
  city-agnostic. `GET /search/_debug` (session-gated) dumps the raw MCP
  text + `get_restaurant_details` for tuning the parser.

### Added
- **Dineout restaurant details (TODO §2).** The Dineout search list only
  has name + rating + locality — so `[DINEOUT]` plan sections were thin.
  `parse_mcp.parse_restaurant_details` reads the `get_restaurant_details`
  "Key: Value" text (cuisine, cost for two, address, timings, deduped
  offers, amenities). `/plans/generate` fetches it for the picked
  restaurant and merges it in before the prompt; `GET /search/restaurant/{id}`
  lets the picker expand a card with the same info when you select it.
- **Picker: refine + "show more" (TODO §2).** If none of the first
  restaurants fit, you can now type what you want ("rooftop", "Italian",
  "quiet & fancy", a name) and re-search, or page through more options.
  `POST /search/` takes `refine` (free text — replaces the derived query
  and boosts name/cuisine/locality matches) and `offset`; the parser
  reports `hasMore` so the button only shows when there's more.
- **Address matching, part 2 (TODO §1).** The typed location now also
  resolves ~90 well-known neighbourhoods to their city (Koramangala →
  Bengaluru, Bandra → Mumbai, Hazratganj → Lucknow, DLF Cyber City →
  Gurugram, …), and both sides of the comparison are expanded — so
  "Whitefield" matches a saved address that only says "Bengaluru", and
  "Bangalore" matches one that only says "Koramangala".
- **"Planning from …"** — the saved Swiggy address a plan was actually
  built from is returned by `POST /search/` (`address_used`), injected
  into the plan prompt, and shown in the picker banner + above the plan.
- `create_address` (add a Swiggy address for an unsaved city) stays
  deferred — see [TODO.md](TODO.md) §1 for why.

---

## [0.9.0] — 2026-09-03

> Consolidation / hardening release. The OAuth 2.1 PKCE flow, live Swiggy
> MCP integration, and the two-step (picker → plan) flow landed in commits
> between 0.8.0 and here without their own changelog entries; 0.9.0 is the
> pass that made the supporting infrastructure honest (real migrations,
> real endpoints, doubled test coverage) and fixed the bugs that review
> surfaced. Shipped as PRs #1–#4.

### Added
- **Chat can now change the plan.** `POST /plans/refine` classifies a
  follow-up as a question or a change. A change returns a sanitised
  `patch` of `PlanRequest` fields (cuisine → `notes`, "vegan guest" →
  `guest_count` + `dietary_tags`, "lower the budget" → `budget`, …); the
  frontend merges it and re-runs generation, keeping the chat thread.
  A question returns a concrete answer grounded in the actual plan
  (no more "ask them for a corner table" filler). `/plans/chat` stays as
  a stream-only advisory endpoint for the legacy Next.js client.
- CI workflow (`pytest` + an `alembic upgrade/downgrade/upgrade` round-trip
  against a Postgres service).
- `docker-compose.yml`, `scripts/setup.sh`, `scripts/seed.py`,
  `docs/{api,deployment,mcp-integration}.md` — were empty.
- Test coverage more than doubled — v2 prompt selection + alcohol,
  `_parse_address_id` / `_location_terms`, MCP query builders, per-service
  cost parsing, refine-patch sanitising, and an orchestrator→prompt
  integration test. **91 passing** (was 45 + 1 failing).

### Fixed
- **Alembic is real now.** The initial migration was empty and `init_db()`
  still ran `SQLModel.metadata.create_all` — Alembic did nothing. The
  migration now carries the full DDL for `users` / `events` / `plans`
  (idempotent: it inspects the DB and only creates missing tables, so it
  is safe on databases that predate Alembic). `create_all` removed from
  `init_db()`. `script.py.mako` imports `sqlmodel` so autogenerated files
  run unedited.
- **Restaurant picker reaches the plan.** `PlanRequest` now has
  `selected_dineout` / `selected_food` (full objects), so the restaurant
  the user picks in Step 2 is actually passed to Claude. Previously the
  frontend sent them but Pydantic dropped them and the plan ignored the pick.
- **Fresh Event per generation.** `/plans/generate` created one demo event
  on first use and reused it forever; the plan's `event_id` pointed at
  frozen config. It now persists the actual request as a new `Event`.
- **Per-service costs persisted.** `dineout_cost` / `food_cost` /
  `instamart_cost` are parsed from the `[COST]` section and written to the
  `plans` row (were always `NULL`). Parser tolerates annotated / split
  cost lines; system prompt tightened to ask for the clean format.
- Hardcoded `Access-Control-Allow-Origin: *` removed from the SSE response
  — `CORSMiddleware` handles it (and `*` is invalid with credentials).
- **Follow-up chat.** The generated plan text is now sent into the chat's
  system prompt, so "Switch to Italian" is understood as cuisine (it was
  answering "I only respond in English") and every reply is grounded in the
  real restaurants/items. Chat SSE now ⏎-encodes newlines and the frontend
  appends chunks verbatim — fixes words jamming together and text vanishing
  after a newline. Removed the "use the regenerate button" advice (there is
  no such button). Chat history resets when a new plan is generated.
- **OAuth return.** After authorising Swiggy the user landed on the app
  root (`/`) instead of where they started. `demo.html` now stashes its
  path before the redirect and the `/auth/callback` page returns there
  (`/demo.html`) with a full navigation. Chat panel `max-height` bumped
  400px → 60vh so long replies aren't clipped.
- **Typed location is now used.** `resolve_addresses` hard-coded
  `preferred_city="Lucknow"` — so with a real Swiggy token every search
  ran against the user's Lucknow saved address no matter what city they
  typed. It now matches the typed city against the user's saved Swiggy
  addresses by **shared city word** (not an exact regex), with a ~35-name
  synonym table for the renamed cities (Gurugram/Gurgaon,
  Bengaluru/Bangalore, Varanasi/Banaras/Kashi, Vizag/Visakhapatnam, …) and
  address-noise words ("sector", "road", …) filtered so they don't cause
  false matches. If nothing matches (and no GPS was given) the plan falls
  back to the default address but says so — `[BRIEF]` acknowledges it and
  the picker shows an amber banner suggesting the user add that city's
  address in the Swiggy app (Swiggy only searches from _saved_ addresses,
  so an unsaved city can't be searched directly).

### Changed
- MCP clients (`food`, `instamart`, `dineout`) now inherit one
  `BaseMCPClient` — HTTP transport, auth headers, 401/419/403 handling in
  one place. Removed the dead `use_mock` / `server_url` / `api_key`
  attributes (real-vs-mock is decided solely by whether a call has an
  access token) and the never-called `_parse_location`.
- `prompts.py`: `build_user_prompt` and `build_user_prompt_v2` collapsed
  into one. v2 was always chosen anyway (`alcohol_preference` always set).
- `offers` / `users` / `orders` endpoints implemented — were mounted but
  empty. `GET /offers/`, `GET /users/me`, `GET /orders/{plan_id}`.
- Model Claude id centralised as `planner.PLAN_MODEL`.

### Removed
- `app/utils/location.py`, `app/utils/dietary.py` — empty, imported nowhere.

---

## [0.8.0] — 2026-04-27

### Added
- 46 unit tests — all passing in 0.10s
- tests/unit/test_planner.py — prompt builders, section markers, MCP injection
- tests/unit/test_offers.py — offer filtering, plan text parsing
- tests/unit/test_orchestrator.py — budget splits, venue mode routing, graceful degradation
- pytest.ini — pythonpath=. for module resolution

### Fixed
- core/config.py — migrated to Pydantic v2 ConfigDict

---

## [0.7.0] — 2026-04-26

### Added
- Alembic migrations replacing create_all()
- alembic/env.py — SQLModel metadata + model imports
- Initial migration: users, events, plans tables

### Changed
- core/database.py — init_db() no longer calls create_all()
- Schema changes now done via: alembic revision --autogenerate + alembic upgrade head

---

## [0.6.0] — 2026-04-26

### Added
- `frontend/src/hooks/useChatStream.ts` — multi-turn chat with conversation history
- `frontend/src/components/plan/ChatPanel.tsx` — chat UI with suggestion chips
- `/plans/chat` endpoint fixed to accept JSON body via ChatRequest schema

### Milestone
Full chat loop working — context-aware follow-up refinement with location-specific responses.

---

## [0.5.0] — 2026-04-26

### Added
- `backend/app/services/plan_service.py` — DB operations for plans (create, update, get, list)
- `backend/app/lib/parse_plan.py` — server-side plan text parser
- `backend/app/schemas/plan_response.py` — PlanReadResponse schema
- Plan persistence wired into generation endpoint — plans saved after streaming completes

### Verified
- Plans saving to Postgres with status=ready, total_cost, total_savings

## [0.4.0] — 2026-04-26

### Added
- Full Next.js frontend with streaming plan UI
- EventForm, GuestRoster, LocationPicker components
- PlanStream with progressive section rendering
- TimelineCard, DineoutCard, FoodCard, InstamartCard, OffersCard, CostCard
- usePlanStream hook managing SSE stream state
- parsePlan utility for section extraction
- SSE buffer fix and ⏎ newline encoding

### Milestone
First complete end-to-end plan visible in browser with all sections rendering correctly.

## [0.3.0] — 2026-04-24

### Added
- `backend/app/schemas/event.py` — EventCreate, EventRead, EventUpdate schemas
- `backend/app/api/v1/endpoints/events.py` — full CRUD (create, list, get, patch, delete)
- `scripts/test_api.sh` — API smoke test script

### Verified
- Event persisted to Postgres with correct fields
- PATCH partial update working (budget updated 5000 → 6000)
- Plan generation working on top of persisted event data

---

## [0.2.0] — 2026-04-24

### Added
- `backend/app/schemas/plan.py` — PlanRequest, Guest, EventType, VenueMode enums
- `backend/app/services/ai/prompts.py` — system + user prompt builders, MCP data injection
- `backend/app/services/ai/planner.py` — Claude streaming plan generator, SSE, follow-up chat
- `backend/app/services/offers/engine.py` — live offer fetch, Redis cache (5 min TTL)
- `backend/app/services/mcp/food.py` — Food MCP client, mock responses
- `backend/app/services/mcp/instamart.py` — Instamart MCP client, event-type product catalog
- `backend/app/services/mcp/dineout.py` — Dineout MCP client, slot availability
- `backend/app/services/mcp/orchestrator.py` — asyncio.gather() parallel coordinator
- `backend/app/api/v1/endpoints/plans.py` — streaming plan + chat endpoints
- `backend/app/api/v1/router.py` — all routers mounted

### Milestone
First successful end-to-end plan generation verified via curl.
Full pipeline operational: parallel MCP → offers → Claude stream → structured SSE output.

---

## [0.1.0] — 2026-04-24

### Added
- Project scaffold — full directory structure (backend, frontend, docs, scripts)
- `README.md` — full product plan, architecture, data flow, tech stack, setup guide
- `.gitignore` — Python, Node, Docker, IDE, env file exclusions
- `.env.example` — all required environment variables documented
- `docker-compose.yml` — local dev stack (FastAPI + Celery + Postgres + Redis + Next.js)
- `.github/workflows/ci.yml` — GitHub Actions CI with pytest + coverage

### Backend — Core
- `backend/app/main.py` — FastAPI app, lifespan context manager, CORS middleware, health endpoint
- `backend/app/core/config.py` — Pydantic settings, all env vars centralised
- `backend/app/core/database.py` — async SQLAlchemy engine, session factory, `init_db()`
- `backend/app/core/redis.py` — async Redis client, singleton pattern, `get_redis()`, `close_redis()`

### Backend — Models
- `backend/app/models/user.py` — User table (phone auth, preferences, dietary tags)
- `backend/app/models/event.py` — Event table (EventType, VenueMode, EventStatus enums, guest roster, location, budget)
- `backend/app/models/plan.py` — Plan table (PlanStatus state machine, MCP data snapshots, cost breakdown, order confirmations)

### Backend — API Stubs
- `backend/app/api/v1/router.py` — API router stub
- `backend/app/api/v1/endpoints/` — empty stubs for plans, events, users, offers, orders

### Infrastructure
- Docker: `soiree-postgres` (PostgreSQL 16) and `soiree-redis` (Redis 7) containers
- Conda env `soiree` with Python 3.12
- All three DB tables verified live in Postgres: `users`, `events`, `plans`
- Server boots clean at `http://localhost:8000`
- API docs live at `http://localhost:8000/docs`

---

## Versioning Strategy

- `0.x.0` — Phase 1 milestones (core features)
- `0.x.x` — incremental additions within a phase
- `1.0.0` — Phase 1 complete (plan & present)
- `2.0.0` — Phase 2 complete (agentic ordering)
- `3.0.0` — Phase 3 complete (corporate + social)