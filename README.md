# Soirée — Life Events Concierge

> An AI concierge that plans a complete evening — restaurant, food delivery, groceries —
> by orchestrating Swiggy's own Dineout, Food, and Instamart MCP servers in parallel, then
> books the table for real.

**Status:** Live — `demo.html` on Vercel, API on Railway, real Postgres + Redis.

**Version:** 1.2.0 — see [CHANGELOG.md](CHANGELOG.md) for full history, [TODO.md](TODO.md) for what's next.

---

## Table of Contents

1. [What This Is](#1-what-this-is)
2. [User Experience](#2-user-experience)
3. [System Architecture](#3-system-architecture)
4. [Technology Stack](#4-technology-stack)
5. [The Plan Generation Pipeline — Step by Step](#5-the-plan-generation-pipeline--step-by-step)
6. [Auth: Swiggy OAuth *Is* the Login](#6-auth-swiggy-oauth-is-the-login)
7. [Real vs. Mock MCP Data](#7-real-vs-mock-mcp-data)
8. [Two-Step Flow: Picker Before Plan](#8-two-step-flow-picker-before-plan)
9. [The SSE Stream's Two Encoding Tricks](#9-the-sse-streams-two-encoding-tricks)
10. [Ordering: Dineout Table Booking](#10-ordering-dineout-table-booking)
11. [Plan History](#11-plan-history)
12. [Analytics and Logging](#12-analytics-and-logging)
13. [Security and Rate Limiting](#13-security-and-rate-limiting)
14. [Production Resilience](#14-production-resilience)
15. [File-by-File Reference](#15-file-by-file-reference)
16. [Database Design](#16-database-design)
17. [Environment Variables](#17-environment-variables)
18. [Running Locally](#18-running-locally)
19. [Deployment](#19-deployment)
20. [CI/CD](#20-cicd)
21. [Test Suite](#21-test-suite)
22. [API Endpoints](#22-api-endpoints)
23. [Key Design Decisions](#23-key-design-decisions)
24. [Known Limitations and Accepted Risks](#24-known-limitations-and-accepted-risks)
25. [Versioning](#25-versioning)
26. [Roadmap](#26-roadmap)
27. [Docs](#27-docs)

---

## 1. What This Is

Two kinds of evenings exist. One where you know exactly where you're eating, and one where
you don't — but either way, planning it today means four separate decisions: pick a
restaurant, decide whether to also order dessert, figure out what else the house needs
(candles, drinks, ice), and remember which app has which offer. Soirée collapses all four
into one conversation.

You describe the event — occasion, venue mode, guest count, budget, time — and Soirée fires
Swiggy's three MCP servers (Dineout, Food, Instamart) **in parallel**, feeds the real,
live results into Claude, and gets back a complete plan: a minute-by-minute timeline, a
specific restaurant with a real bookable slot, food/dessert picks, a grocery cart, active
offers, and a full cost breakdown. Every name, price, and slot in that plan came from a real
Swiggy API call — Claude's job is tone, sequencing, and structure, never invention.

Approve the plan and Soirée books the table for real, via the same `book_table` MCP tool
Swiggy's own apps use — with a mandatory confirmation screen and a 60-second window to
change your mind before anything is sent.

No one else orchestrates the **full evening arc** this way: start at a restaurant
(Dineout), continue at home with delivery and groceries (Food + Instamart), one plan, one
conversation.

---

## 2. User Experience

`frontend/public/demo.html` is the entire frontend — one static HTML file, no build step,
no framework. It runs a five-state flow:

```
┌─────────────┐   fill form    ┌─────────────┐  generate  ┌─────────────┐
│ Empty state │ ─────────────▶ │   Picker    │ ─────────▶ │ Generating  │
│ (landing)   │                │ (Step 1.5)  │            │ (SSE anim)  │
└─────────────┘                └─────────────┘            └──────┬──────┘
                                                                   │
       ┌─────────────┐   history reopen   ┌──────────────┐        │
       │   History   │ ◀────────────────  │     Plan     │ ◀──────┘
       │ (past plans)│                    │ (cards + chat)│
       └─────────────┘                    └──────┬───────┘
                                                    │ Approve & Order
                                            ┌───────▼────────┐
                                            │ Confirm modal  │
                                            │ → 60s undo →   │
                                            │ book_table     │
                                            └────────────────┘
```

**Sign-in** is a single "Sign in with Swiggy" button — phone + OTP on Swiggy's own page, no
Soirée password anywhere (§6).

**The form** collects occasion (Date / Friends / Birthday / Corporate / House Party /
Family), venue mode (Dine Out / Stay In / Hybrid), a guest roster or headcount, budget,
start time, dietary tags, health focus (a slider from indulgent to healthy), alcohol
preference, and a typed or GPS-detected location.

**The picker** (Step 1.5) fetches real restaurant options — no Claude call yet, ~300ms —
and lets you pick a specific Dineout restaurant and a specific Food restaurant before the
plan is generated around your choice. You can refine ("rooftop", "Italian", a name) or page
through more options. Real available time slots show as pills on each Dineout card once
you've connected Swiggy (§10).

**The plan** renders as cards — Timeline, Dineout, Food, Instamart, Offers, Cost — as the
SSE stream completes, followed by a follow-up chat box grounded in the actual plan text: a
question gets an answer, a change request ("switch to Italian", "lower the budget") gets
applied and the plan is silently regenerated in place.

**Approve & Order** opens a confirmation modal (restaurant, time, guest count, cost), then a
60-second countdown banner with an Undo link — nothing is sent to Swiggy until that
countdown reaches zero (§10).

**History** lists your last 20 plans as cards (occasion, location, cost); clicking one
reopens it through the exact same renderer live generation uses, chat included.

---

## 3. System Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│                         frontend/public/demo.html                       │
│         single static file · no build · no framework · Vercel          │
└──────────────────────────────────┬───────────────────────────────────────┘
                                    │ X-Soiree-Session
┌───────────────────────────────────▼───────────────────────────────────────┐
│                          FastAPI (backend/app)                          │
│                                                                          │
│  auth ──▶ Swiggy OAuth 2.1 PKCE ──▶ decode JWT `sub` ──▶ User + session  │
│                                                                          │
│  search ──▶ MCPOrchestrator.gather_context() ──▶ picker cards           │
│                                                                          │
│  plans/generate ──▶ asyncio.gather(Food, Instamart, Dineout, Offers)    │
│               └──▶ build_user_prompt() ──▶ Claude ──▶ SSE ──▶ Postgres  │
│                                                                          │
│  plans/{id}/order ──▶ approve_and_claim_for_ordering() ──▶              │
│               BackgroundTasks.place_dineout_booking() ──▶               │
│               book_with_retry() ──▶ Dineout MCP book_table              │
└───────┬──────────────────┬──────────────────┬───────────────────────────┘
        │                  │                  │
┌───────▼───────┐  ┌───────▼────────┐  ┌──────▼──────────────────────────┐
│   Postgres     │  │     Redis      │  │   Swiggy MCP (or mock)          │
│ users, events, │  │ sessions, the  │  │ Food · Instamart · Dineout      │
│ plans          │  │ encrypted      │  │ real JSON-RPC iff access_token, │
│ (SQLModel +    │  │ Swiggy token,  │  │ else _mock_dispatch — same      │
│ Alembic DDL)   │  │ rate limits,   │  │ response shape either way       │
│                │  │ offer cache    │  │                                 │
└────────────────┘  └────────────────┘  └─────────────────────────────────┘
```

One thing this diagram can't show: every MCP call above takes an `access_token`. If a
caller is logged in but never connected Swiggy (or their 5-day token lapsed), that
parameter is `None` and the exact same code path serves mock data of the identical shape —
there's no separate demo mode to keep in sync (§7).

---

## 4. Technology Stack

### Backend

| Layer | Technology | Why |
|---|---|---|
| API server | **FastAPI** | Async-native, Pydantic built-in, `StreamingResponse` for SSE, `BackgroundTasks` for order placement |
| AI | **Anthropic Python SDK**, `claude-sonnet-4-6` | Native streaming, large context for injecting live MCP JSON as ground truth |
| MCP transport | Hand-rolled JSON-RPC over `httpx` (`services/mcp/base.py`) | Swiggy's MCP servers speak `tools/call` over plain HTTP + Bearer auth — no SDK dependency needed |
| Database | **PostgreSQL** + **SQLModel** | SQLAlchemy + Pydantic fused — one class is both the DB table and (where used) the API schema |
| Migrations | **Alembic**, idempotent by inspection | The DB predates Alembic (was `create_all()`); every migration checks the live schema before altering it, safe on both a fresh DB and the pre-existing production one |
| Cache / sessions | **Redis** | Soirée session tokens, encrypted Swiggy access tokens, offer cache (5 min TTL), rate-limit counters |
| Background jobs | FastAPI **`BackgroundTasks`** (not Celery — see §10) | `celery==5.6.3` sits in `requirements.txt` unused; one bounded MCP call doesn't justify a worker process yet |
| Auth | **Swiggy OAuth 2.1 PKCE** (`services/auth/`) | Login *is* connecting Swiggy — the MCP token's `sub` claim is the durable identity, no separate password |
| Crypto | **Fernet** (`core/crypto.py`) | Encrypts the Swiggy access token at rest in Redis, keyed off `SECRET_KEY` |
| Timezone | **`zoneinfo`** + `tzdata` | Dineout slot dates are IST-specific — UTC's calendar date would be wrong for ~5.5 hours of every IST day |
| Testing | **pytest** + **pytest-asyncio** + **respx** + **Playwright** | Hand-written fakes for unit tests (no DB/Redis fixture); a genuinely live stack for E2E (§21) |
| Logging | Custom `JSONFormatter` (`core/logging.py`) | One JSON object per line for every `app.*` logger; uvicorn's own access/error logs untouched |
| Analytics | **PostHog** (`services/analytics.py`) | Fully opt-in — unset `POSTHOG_API_KEY` means the `posthog` package is never even imported |

### Frontend

| Layer | Technology | Why |
|---|---|---|
| Web | `frontend/public/demo.html` — one static file | No build step, no framework, no bundler — the entire UI is inline `<style>` + vanilla JS |
| Styling | Inline CSS custom properties, Cormorant Garamond (display) + DM Sans (body) | A single file needed a single, portable styling approach |
| Streaming | Native `fetch` + `ReadableStream`, hand-parsed SSE | No `EventSource` — its GET-only, no-custom-headers constraints don't fit a POST-with-auth-header streaming endpoint |
| OAuth callback shell | Next.js 14 (App Router), `frontend/src/` | Hosts only `/auth/callback` + `/callback` — the Swiggy-whitelisted redirect URIs — not a second app (§6) |

### Infrastructure

| Layer | Technology | Why |
|---|---|---|
| API hosting | **Railway** | Single Docker service, `alembic upgrade head && uvicorn ...` as the start command |
| Frontend hosting | **Vercel** | Serves `frontend/public/` as static assets via the minimal Next.js shell |
| Local dev | **Docker Compose** (`db` + `redis` services) | `docker compose up -d db redis`, matches `backend/.env.example` |
| Python env | **Conda**, `soiree` env, Python 3.12 | Matches the Docker image's Python version |
| CI/CD | **GitHub Actions** | `pytest -q` + an Alembic `upgrade → downgrade base → upgrade` round-trip on every push/PR to `main` |

---

## 5. The Plan Generation Pipeline — Step by Step

### Step 1 — Form submission → picker (`POST /search/`)

The frontend builds a `SearchRequest` and posts it. `MCPOrchestrator.gather_context()`
resolves the user's saved Swiggy address from the typed location (a large synonym +
neighbourhood table handles renamed cities and areas like Koramangala → Bengaluru), then
fires whichever of Food/Instamart/Dineout the `venue_mode` needs — concurrently, via
`asyncio.gather()`. No Claude call. Typical latency: ~300ms.

**Why a picker before the plan at all?** The old single-step flow let Claude pick a
restaurant from raw MCP data — occasionally producing a plan built around a restaurant the
user wouldn't have chosen. The picker gives the user control and gives Claude a *specific*
restaurant to write around, which also means offers and slot data can be fetched for that
exact pick rather than a generic search.

### Step 2 — Restaurant/slot selection

The user picks (or skips) a Dineout and/or Food restaurant. If Swiggy is connected, real
available slots (from `get_available_slots`, never cached — "slots change in real time as
others book") show on the Dineout card.

### Step 3 — Plan generation (`POST /plans/generate`, SSE)

```
event_data = PlanRequest.model_dump()
      │
      ▼
asyncio.gather(
    MCPOrchestrator.gather_context(...),      # Food + Instamart + Dineout, parallel
    OffersEngine.get_active_offers(...),       # Redis-cached, 5 min TTL
)
      │
      ▼
_enrich_dineout()  — if a Dineout restaurant was picked AND Swiggy is connected:
      │              get_restaurant_details() (cuisine, cost, amenities, real offers)
      │              + get_available_slots() (today, IST) → merged into selected_dineout
      ▼
resolved["dineout"] captured — restaurant_id, name, date, guest_count, start_hour,
      │              available_slots — this is what order placement re-derives a
      │              real slotId from later (§10); Claude's own prose has no
      │              structural link back to one specific slot
      ▼
build_system_prompt() + build_user_prompt()
      │              static persona/format + dynamic MCP JSON, event config, offers —
      │              Claude treats the MCP JSON as ground truth, never invents names
      ▼
Claude claude-sonnet-4-6 — one non-streaming .create() call, full response collected
      │              (token-by-token streaming fragmented [MARKER] section headers
      │              across SSE frames — see §9)
      ▼
SSE stream to the browser — newlines encoded as ⏎ (§9), heartbeat comments on any
      │              30-60s gap so Railway's HTTP/2 edge doesn't reset the connection
      ▼
parse_plan_text() (server) / parse() (demo.html) — same [MARKER] extraction both sides
      │
      ▼
Plan row saved: status=ready, per-service + total costs, dineout_selection JSON
```

Budget is split by `venue_mode`: `out` → 100% Dineout, `home` → 70% Food / 30% Instamart,
`hybrid` → 60% Dineout / 20% Food / 20% Instamart (Food limited to dessert/celebration
items in hybrid mode — never a full second meal).

### Step 4 — Follow-up chat (`POST /plans/refine`)

Stateless — takes `plan_text` + `conversation_history` + the original `event_data`, no
`plan_id` needed. Claude classifies the message as `answer` (a specific reply, grounded in
the plan text) or `modify` (a sanitised `PlanRequest` patch — only `notes`, `budget`,
`guest_count`, `dietary_tags`, `alcohol_preference`, `venue_mode`, `health_focus`,
`start_hour` are patchable, values clamped). A `modify` is merged client-side and
`/plans/generate` re-runs, keeping the chat thread.

---

## 6. Auth: Swiggy OAuth *Is* the Login

There is no separate Soirée account, no password, no phone-OTP, no SMS provider.

```
GET  /auth/start?consent=true          (public)
     → PKCE challenge + state cached 2 min in Redis
     → { authorize_url }
                                        user authorises on Swiggy's own page
POST /auth/callback { code, state }    (public)
     → exchange code for the MCP access token (a JWT)
     → decode_token_identity(token) reads its `sub` claim WITHOUT verifying
       the signature — safe because the token came straight from Swiggy's
       own token endpoint over TLS, never from a client
     → get-or-create a User keyed on swiggy_sub
     → encrypt + store the Swiggy token: swiggy_token:{user_id} (Redis, 5-day)
     → mint a 30-day Soirée session: soiree_session:{token} (Redis)
     → { soiree_session, user, is_new, swiggy_expires_at }
```

`soiree_session` rides every subsequent request as **`X-Soiree-Session`** →
`deps.current_user` → `401 {"code": "NOT_LOGGED_IN"}` without it. That's the
**one** login. The Swiggy access token is a **separate**, shorter-lived thing — 5 days, no
refresh. A user can have a perfectly live 30-day session with a lapsed Swiggy token;
`GET /auth/status` reports `{connected: false}` in that case and the UI shows a "Reconnect
Swiggy" chip (same OAuth flow, not a full re-login).

**Proactive reconnect nudge**: `checkSwiggyAuth()` keeps `expires_at` from `/auth/status`
and shows the reconnect chip once the token is within 24h of expiring — not just once it's
already lapsed — with a label ("Swiggy expiring soon" vs "Reconnect Swiggy") and login-modal
message that distinguish the two cases.

The two whitelisted OAuth redirect URIs (`/auth/callback` on the production domain,
`/callback` for local dev) are the *only* reason `frontend/src/`'s minimal Next.js shell
still exists — see §4's frontend table and `CLAUDE.md`.

---

## 7. Real vs. Mock MCP Data

One switch, decided per call, not a global flag:

```python
# services/mcp/base.py — BaseMCPClient._call_mcp
if not access_token:
    return await self._mock_dispatch(tool_name, params)
return await self._real_mcp_call(tool_name, params, access_token)
```

A logged-in user with no Swiggy connection transparently gets mock data of the **exact same
response shape** as a real call — there's no `USE_MOCK` setting anywhere, and no code path
that only exists for the mock case. This is also why the E2E suite (§21) can exercise the
entire golden path — form → picker → plan → refine → order — without a real Swiggy account:
it just never connects one.

Real MCP responses are **human-readable text**, not structured JSON (`{"result":
{"content": [{"type": "text", "text": "Found 10 restaurants..."}]}}`). `services/mcp/
parse_mcp.py` normalises three different real-text conventions — Food's embedded JSON blob,
Dineout's numbered text lines, Dineout's "Key: Value" restaurant-details format — into the
same shape the mocks emit. Two of those parsers (`parse_restaurant_list`,
`parse_restaurant_details`) were tuned against real output captured via `GET
/search/_debug`; `parse_available_slots` and `parse_booking` are still **unconfirmed** —
written tolerant of a couple of plausible conventions, pending a live Swiggy token to test
against.

---

## 8. Two-Step Flow: Picker Before Plan

Covered in §5 (Step 1–2). The short version: `POST /search/` is fast and Claude-free,
existing specifically so the user picks a *real* restaurant before the (slower, Claude-in-
the-loop) `POST /plans/generate` call writes a plan around it. Picking nothing is fine too —
Claude then selects from the raw MCP data per the system prompt's rules.

---

## 9. The SSE Stream's Two Encoding Tricks

**1. `⏎` newline proxy encoding.** Claude's output contains real newlines, and a bare `\n`
in an SSE stream is a message-separator, not payload — section markers like `[TIMELINE]`
were arriving on empty frames without a `data:` prefix and getting silently dropped. Fix:
`\n` → `⏎` before sending, decoded back to `\n` client-side. This is also why plan
generation collects the *full* Claude response with one `.create()` call rather than
streaming token-by-token — mid-token SSE chunk boundaries would split a `⏎`-encoded
sequence unpredictably.

**2. Heartbeat comments on long silences.** `generate_plan()`'s pipeline (parallel MCP
calls, then one non-streaming Claude call) can be silent for 30-60 seconds. Railway's edge
proxy resets an HTTP/2 stream that goes that long without bytes, so `plans.py::
_with_heartbeat` wraps the generator and injects an SSE comment (`: ping\n\n`) on any gap —
the frontend parser already ignores any line that doesn't start with `data:`.

---

## 10. Ordering: Dineout Table Booking

`POST /plans/{plan_id}/order` books a real table via Dineout's `book_table` — the one
service actually wired up. Food (`place_food_order`) and Instamart (`checkout`) aren't
built: neither has real, selectable item IDs anywhere in the pipeline yet (the picker only
ever carries dish *names* for Food, and there's no product-selection step for Instamart at
all) — that's a genuinely new frontend UX project, tracked in [TODO.md](TODO.md) §4, not a
backend wire-up.

### The gap this closed

The picker/plan pipeline resolved a real restaurant + slot at generation time but discarded
it afterward — only Claude's prose made it into the DB. `generate_plan(..., resolved=...)`
(an out-param, same pattern as the existing `usage` token-count out-param) now captures it
into `Plan.dineout_selection`. Order placement always **re-fetches slots live** rather than
trusting the stored ones (`parse_mcp.closest_slot()` picks whichever is nearest the plan's
`start_hour`) — matching `dineout.py`'s own "never cache slot data" rule.

### Retry design — `book_table` is not idempotent

Swiggy documents no idempotency-key parameter for `book_table`, and its own advice ("on a
5xx, call `get_booking_status` before retrying") only works once you already *have* a
`bookingId` — which a 5xx on the very first attempt, by definition, never gave you.
`services/orders/dineout_ordering.py::book_with_retry` is built around that gap:

| Failure | Response |
|---|---|
| 401/419/403 | Stop immediately — re-auth needed, not a retry |
| 4xx / JSON-RPC error | Swiggy rejected the request outright (e.g. a stale `slotId`) — exactly **one** corrective retry against a freshly re-fetched slot |
| 5xx / timeout | Genuinely ambiguous. Try to recover a `bookingId` from the failed response body → if found, `get_booking_status()` settles it. If not, re-check the *same* `slotId` via a fresh `get_available_slots()` — still there → safe to retry; gone, or the re-check itself fails → **stop**, don't retry (can't tell if that's us or someone else, and guessing wrong risks a double booking) |

This is a heuristic, not proof — see the module's own docstring for the full reasoning. The
only idempotency guard on Soirée's *own* side is `plan_service.
approve_and_claim_for_ordering()` — an atomic `UPDATE plans SET status='ordering' WHERE
status='ready'` — which stops Soirée's own system from double-submitting (double-click, a
duplicate task), independent of anything Swiggy does or doesn't guarantee.

### Confirmation, and a pre-send (not post-send) undo

There's no confirmed `cancel_booking` tool in Dineout's documented tool list, so a
post-booking "undo" isn't something that can be built honestly right now. Instead: a
mandatory confirmation modal, then a 60-second countdown banner — `book_table` is only
called once that countdown reaches zero. Undo during the window means the request is simply
never sent, not that a real booking gets cancelled.

### Why `BackgroundTasks`, not Celery

`celery==5.6.3` has sat in `requirements.txt` since early on as a forward-looking
dependency — still genuinely unused. Railway currently deploys exactly one process
(`alembic upgrade head && uvicorn ...`); standing up Celery for real means a second
service, its own broker wiring, its own restart story. One bounded MCP call (worst case
~15-20s across retries) doesn't justify that yet. `place_dineout_booking` always terminates
the plan in `confirmed` or `failed` inside a try/except — the one accepted failure mode is a
process restart mid-task leaving a plan stuck in `ordering`, which is visible and bounded,
not silent. Revisit Celery once Food + Instamart also need background execution — three
parallel MCP writes with independent retry/backoff is a much better fit for Celery's
primitives than one call is.

---

## 11. Plan History

`GET /plans/history` returns the last 20 `ready` plans as lightweight summaries —
`event_type`/`location`/`guest_count` joined from the parent `Event` (a `Plan` row alone
doesn't carry them) alongside the cost breakdown. `demo.html`'s History screen renders them
as cards; clicking one fetches the full `GET /plans/{plan_id}` row and adapts it
(`planFromRecord()`) onto the exact shape `renderPlan()` already expects from live
generation — same renderer, same follow-up chat (`plan_text` is reconstructed with
`[MARKER]` delimiters from the stored fields so `/plans/refine` grounds identically). `brief`
isn't persisted anywhere (it only ever existed in the SSE stream), so a reopened historical
plan renders without that one line.

---

## 12. Analytics and Logging

**Analytics** (`services/analytics.py`) wraps PostHog behind `POSTHOG_API_KEY`. Unset (the
default) means every function is a true no-op — the `posthog` package is never even
imported, no client is constructed, no network call is ever made. Every call site
(`auth.py`, `search.py`, `plans.py`) calls `analytics.capture(...)` unconditionally; the
module absorbs its own failures so a PostHog outage can never affect a real request. The
pre-login funnel step (`swiggy_auth_started`) uses the OAuth `state` as a temporary
distinct_id; `analytics.alias()` merges it onto the real `user.id` once `/auth/callback`
resolves one. Anthropic token usage (`generate_plan`/`refine_plan`'s `usage` out-param) rides
on `plan_generated`/`chat_message` as raw `tokens_in`/`tokens_out` counts — never converted
to a $ figure server-side, since per-token pricing changes over time and is better computed
downstream against current rates.

**Logging** (`core/logging.py::configure_logging()`) routes every `app.*` logger through a
`JSONFormatter` — one JSON object per line (`timestamp`, `level`, `logger`, `message`, plus
anything passed via `extra={...}`). Wired once at the top of `main.py`, before the module's
own `logger = logging.getLogger(__name__)`. It does not touch uvicorn's own access/error
logs — those are separate loggers with `propagate=False`.

---

## 13. Security and Rate Limiting

- **`SECRET_KEY` guard** — the app refuses to boot in `APP_ENV=production` while it's still
  the default string.
- **Swiggy tokens encrypted at rest** — Fernet, key derived from `SECRET_KEY`
  (`core/crypto.py`); `decrypt()` passes pre-existing plaintext through so a rotation never
  breaks live sessions.
- **Rate limiting** (`core/ratelimit.py`) — Redis fixed-window, keyed on `X-Soiree-Session`
  first, then a legacy Swiggy session header, then client IP. Fails **open** if Redis is
  down (a rate limiter that fails closed would take down the whole API on a Redis blip).
  Limits: 25 `/plans/generate`, 40 `/plans/refine` + `/plans/chat`, 90 `/search/`, 10
  `/plans/{id}/order` per hour; `/auth/start` + `/auth/callback` capped at 30/hr per IP.
- **`/docs`, `/redoc`, `/openapi.json`** disabled in `APP_ENV=production`.
- **MCP error taxonomy** — 401/419/403 from Swiggy normalise to a `PermissionError` subtype
  in `base.py`, mapped to specific frontend re-auth actions in `docs/mcp-integration.md`.
- **Consent gate** — `GET /auth/start` requires `consent=true` server-side (not just a
  frontend checkbox); the privacy-policy acceptance timestamp travels through the PKCE
  record and lands on `users.consent_accepted_at` at callback time.

---

## 14. Production Resilience

| Failure mode | Handling |
|---|---|
| A single MCP service errors | `MCPOrchestrator` degrades per-service — if Dineout fails, Food + Instamart results still return, the plan is built from whatever succeeded |
| Redis is down | Rate limiter fails open; sessions/cache reads fail loudly where they're load-bearing (session lookup), silently where they're not (offer cache) |
| PostHog is down | `analytics.py` absorbs the exception — a request never fails because analytics did |
| Railway's edge kills a long-silent SSE stream | `_with_heartbeat` (§9) |
| A `book_table` 5xx | `book_with_retry`'s ambiguous-failure resolution (§10) — never blindly retries into a possible double booking |
| The process restarts mid-`BackgroundTasks` order | Plan is left in `ordering` with no further update — visible via `GET /orders/{plan_id}`, bounded, not silent (accepted trade-off, §10) |
| A migration runs against the pre-Alembic production DB | Every migration inspects the live schema before altering it — safe on a fresh DB and the old `create_all()`-built one alike |
| Claude's JSON response is truncated or malformed | Plan generation's parser has fallback recovery; `refine_plan`'s classifier falls back to `answer`-only if a `modify` patch can't be sanitised |

---

## 15. File-by-File Reference

```
soiree/
├── backend/
│   ├── app/
│   │   ├── main.py                        # FastAPI app, lifespan, CORS, configure_logging()
│   │   ├── core/
│   │   │   ├── config.py                  # Pydantic Settings — every env var, one place
│   │   │   ├── database.py                # Async SQLAlchemy engine, AsyncSessionLocal, get_session
│   │   │   ├── redis.py                   # Async Redis singleton client
│   │   │   ├── crypto.py                  # Fernet encrypt/decrypt for the Swiggy token at rest
│   │   │   ├── ratelimit.py               # Redis fixed-window rate limiter, fails open
│   │   │   └── logging.py                 # JSONFormatter + configure_logging()
│   │   ├── models/                        # SQLModel DB tables
│   │   │   ├── user.py                    # User — swiggy_sub (unique), swiggy_user_id, consent
│   │   │   ├── event.py                   # Event — occasion, venue, guests, budget
│   │   │   └── plan.py                    # Plan — MCP snapshots, timeline, costs, order IDs
│   │   ├── schemas/                       # Pydantic request/response shapes
│   │   │   ├── event.py                   # EventCreate, EventRead, EventUpdate
│   │   │   └── plan.py                    # PlanRequest, SearchRequest, Guest, EventType, VenueMode
│   │   ├── api/v1/
│   │   │   ├── router.py                  # Mounts every endpoint router under /api/v1
│   │   │   ├── deps.py                    # current_user — the auth gate (401 without a session)
│   │   │   └── endpoints/
│   │   │       ├── auth.py                # Swiggy OAuth = login: start / callback / status / logout
│   │   │       ├── search.py              # POST /search/ — picker discovery, /_debug, /restaurant/{id}
│   │   │       ├── events.py              # CRUD, owned by current_user
│   │   │       ├── plans.py               # SSE generate/chat/refine, history, order placement
│   │   │       ├── users.py               # GET /users/me, logout, DELETE /users/me (data purge)
│   │   │       ├── offers.py              # GET /offers/ — public, no login required
│   │   │       └── orders.py              # GET /orders/{plan_id} — order/booking status, read-only
│   │   ├── lib/
│   │   │   └── parse_plan.py              # Server-side [MARKER] section parser
│   │   ├── services/
│   │   │   ├── plan_service.py            # create_plan, update_plan_text, approve_and_claim_for_ordering
│   │   │   ├── analytics.py               # PostHog wrapper — no-op without POSTHOG_API_KEY
│   │   │   ├── auth/
│   │   │   │   ├── session.py             # Soirée session tokens in Redis
│   │   │   │   └── oauth.py               # PKCE, DCR, token exchange, decode_token_identity
│   │   │   ├── mcp/
│   │   │   │   ├── base.py                # BaseMCPClient — shared JSON-RPC transport + real/mock switch
│   │   │   │   ├── orchestrator.py        # asyncio.gather() across all 3 MCPs, address resolution
│   │   │   │   ├── parse_mcp.py           # Normalises real MCP text into the mocks' shape
│   │   │   │   ├── food.py                # Food MCP client + mocks
│   │   │   │   ├── instamart.py           # Instamart MCP client + mocks
│   │   │   │   └── dineout.py             # Dineout MCP client + mocks — search, slots, book_table
│   │   │   ├── orders/
│   │   │   │   └── dineout_ordering.py    # book_with_retry — the non-idempotent retry state machine
│   │   │   ├── ai/
│   │   │   │   ├── planner.py             # generate_plan / refine_plan — Claude + SSE encoding
│   │   │   │   └── prompts.py             # System + user prompt builders
│   │   │   └── offers/
│   │   │       └── engine.py              # Live offer fetch, Redis cache (5 min TTL)
│   │   └── workers/
│   │       └── tasks.py                   # place_dineout_booking — runs via FastAPI BackgroundTasks
│   ├── tests/unit/                        # Hand-written fakes — no real DB/Redis (see §21)
│   ├── tests_e2e/                         # Genuinely live stack — real Postgres/Redis/browser
│   ├── alembic/versions/                  # Idempotent-by-inspection migrations
│   ├── requirements.txt / requirements-e2e.txt
│   ├── Dockerfile
│   └── .env.example
├── frontend/
│   ├── public/
│   │   ├── demo.html                      # The entire frontend
│   │   └── privacy.html                   # Privacy policy (draft)
│   └── src/app/
│       ├── page.tsx                       # / — redirects to /demo.html
│       ├── auth/callback/page.tsx         # Swiggy OAuth redirect target (whitelisted URI)
│       └── callback/page.tsx              # Redirect shim → /auth/callback (local-dev URI)
├── docs/
│   ├── api.md                             # Full endpoint reference (request/response shapes)
│   ├── deployment.md                      # Railway / Vercel / migrations / OAuth redirect URIs
│   └── mcp-integration.md                 # Swiggy MCP transport, address resolution, error taxonomy
├── scripts/
│   ├── setup.sh                           # One-shot local setup — Docker, deps, migrations, seed
│   ├── seed.py                            # Mint a dev session without real Swiggy OAuth
│   ├── peek_token.py                      # Dump a stored Swiggy token's JWT claims
│   └── relink_user.py                     # Move a pre-OAuth account's data onto the new row
├── docker-compose.yml
├── CHANGELOG.md
├── TODO.md
└── CLAUDE.md                              # Guidance for Claude Code sessions working in this repo
```

---

## 16. Database Design

### `users`

| Column | Type | Notes |
|---|---|---|
| `id` | `VARCHAR` PK | UUID, generated in Python |
| `swiggy_sub` | `VARCHAR` UNIQUE | The durable identity — from the Swiggy MCP token's JWT `sub` claim |
| `swiggy_user_id` | `VARCHAR` | Swiggy's own customer id |
| `phone` | `VARCHAR` NULLABLE | Nullable since the OAuth pivot — no longer collected at signup |
| `name`, `email` | `VARCHAR` NULLABLE | |
| `default_city` | `VARCHAR` NULLABLE | |
| `consent_accepted_at` | `TIMESTAMP` NULLABLE | Privacy-policy acceptance, stamped at `/auth/callback` |
| `created_at`, `updated_at`, `last_login_at` | `TIMESTAMP` | |

### `events`

| Column | Type | Notes |
|---|---|---|
| `id` | `VARCHAR` PK | |
| `user_id` | `VARCHAR` FK → users | |
| `event_type` | `ENUM` | `date` / `friends` / `birthday` / `corporate` / `house_party` / `family` |
| `venue_mode` | `ENUM` | `out` / `home` / `hybrid` |
| `location`, `latitude`, `longitude` | | Typed text or GPS-resolved |
| `start_hour` | `FLOAT` | 24h, 10–23, half-hour steps |
| `budget`, `guest_count` | `INT` | |
| `guests` | `VARCHAR` NULLABLE | JSON: `[{"name", "dietary_tags"}]` |
| `dietary_tags` | `VARCHAR` NULLABLE | JSON, group-level |
| `health_focus` | `INT` | 0 = indulgent, 100 = healthy |

### `plans`

| Column | Type | Notes |
|---|---|---|
| `id` | `VARCHAR` PK | |
| `event_id`, `user_id` | FK | `user_id` denormalised for fast "all my plans" queries |
| `status` | `ENUM` | `generating → ready → ordering → confirmed ↘ failed` (§10 — `approved` still exists in the enum but the current UI collapses `ready→ordering` into one atomic claim) |
| `timeline` | `VARCHAR` NULLABLE | JSON: `[{time, emoji, title, detail}]` |
| `dineout_options`, `food_options`, `instamart_cart` | `VARCHAR` NULLABLE | Claude's free-text `[DINEOUT]`/`[FOOD]`/`[INSTAMART]` prose |
| `dineout_selection` | `VARCHAR` NULLABLE | **New in v1.2.0** — JSON snapshot of the resolved booking target: `{restaurant_id, name, date, guest_count, start_hour, available_slots}` (§10) |
| `active_offers`, `health_insight` | `VARCHAR` NULLABLE | |
| `dineout_cost`, `food_cost`, `instamart_cost`, `total_cost`, `total_savings` | `INT` NULLABLE | Parsed from Claude's `[COST]` section into queryable integers |
| `dineout_booking_id`, `food_order_id`, `instamart_order_id` | `VARCHAR` NULLABLE | Only `dineout_booking_id` is ever populated today |
| `order_error` | `VARCHAR` NULLABLE | **New in v1.2.0** — machine-readable failure code when `status=failed` |
| `edit_count`, `last_edited_at` | | Chat-driven regenerations |
| `created_at`, `approved_at` | | |

```
users (1) ──── (many) events
events (1) ──── (many) plans     ← regenerating creates a new Plan row, same Event
```

All schema changes go through Alembic (`backend/alembic/versions/`), idempotent by
inspection — see `CLAUDE.md`'s note on why, and `docs/deployment.md` for the current
production migration state.

---

## 17. Environment Variables

Copy `backend/.env.example` to `backend/.env` and fill in. Every var maps 1:1 to a field on
`Settings` in `app/core/config.py`.

| Var | Required | Notes |
|---|---|---|
| `APP_ENV` | No, default `development` | `production` enables the `SECRET_KEY` guard and disables `/docs` |
| `SECRET_KEY` | Only in production | App refuses to boot in `production` with the default value |
| `ALLOWED_ORIGINS` | No | JSON array or comma-separated string; CORS echoes the request Origin when it's in this list |
| `DATABASE_URL` | Yes | `postgresql://` is auto-rewritten to `+asyncpg` |
| `REDIS_URL` | Yes | `rediss://` (TLS) auto-detected |
| `ANTHROPIC_API_KEY` | Yes | Plan generation and refine won't work without it |
| `REDIRECT_URI` | Yes | Must be whitelisted with Swiggy — see `docs/deployment.md` |
| `SWIGGY_API_KEY`, `SWIGGY_MCP_*_URL` | No | Omitted → every MCP call falls back to mock data (§7) |
| `POSTHOG_API_KEY`, `POSTHOG_HOST` | No | Unset → analytics fully off (§12) |

---

## 18. Running Locally

### Prerequisites
Python 3.12 (Conda recommended), Docker Desktop, Node.js 20+ (only needed for the stale
Next.js shell's `npm install` — `demo.html` itself needs no build step).

### One-shot setup

```bash
git clone https://github.com/thebluntcoder/soiree.git
cd soiree
./scripts/setup.sh
# brings up Postgres + Redis, installs deps, applies migrations, seeds a demo session
```

### Or step by step

```bash
docker compose up -d db redis
cd backend
cp .env.example .env        # fill in ANTHROPIC_API_KEY
pip install -r requirements.txt
alembic upgrade head
python ../scripts/seed.py   # mints a dev session — no real Swiggy OAuth needed
uvicorn app.main:app --reload
# API at http://localhost:8000, docs at /docs
```

Open `frontend/public/demo.html` directly in a browser, or serve it:

```bash
cd frontend && npm run dev   # only needed for the Next.js shell; demo.html needs no server
```

### Verify

```bash
curl http://localhost:8000/health
# {"status": "ok", "service": "soiree-api", "version": "1.2.0"}
```

### Common commands

```bash
# tests
cd backend && pytest -q                                  # everything (unit)
cd backend && pytest tests/unit/test_auth.py -q           # one file

# E2E (Playwright, live local stack) — NOT part of pytest -q
cd backend && pip install -r requirements-e2e.txt && playwright install chromium
cd backend && pytest tests_e2e -q

# migrations
cd backend && alembic upgrade head
cd backend && alembic revision --autogenerate -m "description"
cd backend && alembic downgrade -1

# inspect a stored Swiggy token's JWT claims (decodes, never verifies signature)
cd backend && python ../scripts/peek_token.py
```

---

## 19. Deployment

**Backend** — Railway, single Docker service. `railway.toml`'s start command runs
`alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port $PORT` — migrations
apply on every deploy, safe against the pre-Alembic production DB because every migration
inspects the schema first.

**Frontend** — Vercel, serving `frontend/public/` (including `demo.html`) as static assets
via the minimal Next.js shell described in §6.

Full details — required env vars, the OAuth redirect URIs whitelisted with Swiggy, and the
post-deploy `relink_user.py` step for migrating pre-OAuth accounts — are in
[docs/deployment.md](docs/deployment.md).

---

## 20. CI/CD

`.github/workflows/ci.yml` runs two jobs on every push/PR to `main`:

1. **`backend-tests`** — `pip install -r requirements.txt && pytest -q`
2. **`migrations`** — against a real `postgres:16-alpine` service container:
   `alembic upgrade head && alembic downgrade base && alembic upgrade head`

A migration that only works in one direction, or an idempotency guard that isn't actually
idempotent, fails this. The E2E suite (Playwright, §21) is **not** part of CI today — it
needs a live local stack (Postgres + Redis + a real browser) that isn't wired up as a CI
service yet.

---

## 21. Test Suite

**240 unit tests**, hand-written fakes throughout — no `aiosqlite` fixture, no `fakeredis`
dependency. Tests that need Redis-like behavior build a small dict-backed fake class inline
and `monkeypatch` the module's `get_redis`; MCP-touching business logic (`_enrich_dineout`,
`book_with_retry`) mocks the relevant client methods directly with `AsyncMock`; endpoint
functions are called directly with stub `db`/`user` objects rather than going through
FastAPI's test client. `respx` mocks the two outbound OAuth HTTP calls
(`register_client`, `exchange_code_for_token`).

**7 E2E tests** (`backend/tests_e2e/`, Playwright) are the deliberate exception — a
genuinely live stack: the real FastAPI app in a background thread, a real (throwaway)
Postgres database, real Redis, a real Chromium browser driving `demo.html` over real HTTP.
Only Claude is mocked (monkeypatching `planner._get_clients`) — everything else, including
plan persistence, session handling, and the mock-branch of `book_table`, runs for real. Two
fixed ports are load-bearing: the live server binds **8000** because `demo.html` hardcodes
`API_BASE` to it, and the static file server binds **3000** because that's the only origin
in `ALLOWED_ORIGINS` by default. Lives outside `pytest.ini`'s `testpaths`, so run it
explicitly: `pytest tests_e2e -q` (after `pip install -r requirements-e2e.txt && playwright
install chromium`).

```
tests/unit/
├── test_auth.py            # Session lifecycle, current_user gate, /auth/callback
├── test_oauth.py           # PKCE generation, authorize URL, token exchange (respx)
├── test_search.py          # GET /search/restaurant/{id}'s slot-merging
├── test_orders_endpoint.py # POST /plans/{plan_id}/order's branching
├── test_dineout_mcp.py     # book_table / get_booking_status, mock + real-call shape
├── test_dineout_ordering.py# book_with_retry's full retry state machine
├── test_parse_mcp.py       # Every MCP text-normalisation parser
├── test_planner.py         # Prompt builders, _enrich_dineout, refine_plan
├── test_orchestrator.py    # Address resolution, per-service degradation
├── test_offers.py          # Offer engine cache behavior
├── test_analytics.py       # PostHog wrapper no-op-by-default behavior
├── test_logging.py         # JSONFormatter output shape
├── test_security.py        # SECRET_KEY guard, rate-limiter precedence
└── test_plans_stream.py    # SSE heartbeat behavior

tests_e2e/
├── conftest.py              # Live-stack fixtures — background-thread uvicorn, throwaway DB/Redis
├── test_plan_flow.py        # form → picker → plan → refine → history → approve & order
└── test_auth_flow.py        # Proactive Swiggy-reconnect nudge
```

---

## 22. API Endpoints

Full request/response shapes, error codes, and the auth model live in
**[docs/api.md](docs/api.md)**. Summary:

| Router | Endpoints |
|---|---|
| `auth` | `GET /auth/start` · `POST /auth/callback` · `GET /auth/status` · `POST /auth/logout` |
| `search` | `POST /search/` · `GET /search/restaurant/{id}` · `GET /search/_debug` |
| `events` | `POST /events/` · `GET /events/` · `GET /events/{id}` · `PATCH /events/{id}` · `DELETE /events/{id}` |
| `plans` | `POST /plans/generate` (SSE) · `POST /plans/chat` (SSE, legacy) · `POST /plans/refine` · `GET /plans/event/{id}` · `GET /plans/history` · `GET /plans/{id}` · `POST /plans/{id}/order` |
| `users` | `GET /users/me` · `POST /users/logout` · `DELETE /users/me` |
| `offers` | `GET /offers/` (public) |
| `orders` | `GET /orders/{plan_id}` |

Plus `GET /health` at the root (not under `/api/v1`).

---

## 23. Key Design Decisions

**Parallel MCP calls over serial.** All relevant Swiggy MCP servers fire simultaneously via
`asyncio.gather()`. A hybrid event completes in the time of the slowest single call (~300ms)
instead of the sum of all three (~750ms).

**Live data, AI reasoning — never the reverse.** Every restaurant name, price, and slot in a
plan comes from a real Swiggy MCP call. Claude's system prompt has an explicit grounding
rule: never invent a restaurant. This is enforced by giving Claude the MCP JSON as ground
truth, not by hoping it behaves.

**Collect-then-send SSE, not token streaming.** Explained in §9 — mid-token chunk boundaries
fragmented `[MARKER]` section headers unpredictably. The trade-off is a ~5s wait, then an
instant full render, masked by the frontend's generating animation.

**One session model, not two.** Before the OAuth pivot, Soirée had its own phone-OTP account
system *and* a Swiggy connection — two logins for one product. Collapsing them into "login
is Swiggy OAuth" removed an entire auth subsystem (`services/auth/otp.py`, `/users/otp/*`,
an SMS provider) and the friction of asking a user to authenticate twice.

**Idempotent migrations, not a clean-slate assumption.** The production DB predates Alembic
— it was built by `SQLModel.metadata.create_all()`. Every migration inspects the live
schema before altering it, so `alembic upgrade head` is safe against both a fresh DB and the
one that's been running in production since before migrations existed.

**Stop, don't guess, when a non-idempotent call's outcome is ambiguous.** `book_with_retry`
(§10) would rather leave a booking in a state that needs a human to check the Swiggy app
than retry into a possible double booking. This is the one place in the codebase where "be
helpful" is deliberately subordinate to "don't cause harm."

---

## 24. Known Limitations and Accepted Risks

| Limitation | Why it's accepted for now |
|---|---|
| Food and Instamart ordering aren't built | Both need a real dish/product picker UI — a separate project, not a backend gap (§10, TODO.md §4) |
| `parse_available_slots` / `parse_booking` are unconfirmed against live MCP output | No Swiggy token was available to test against while building them; written tolerant of a couple of plausible text conventions, same iteration loop the other two parsers already went through |
| A `BackgroundTasks` order job is lost on a mid-task process restart | Visible (`GET /orders/{plan_id}` shows `ordering` with no further update) and bounded, not silent; Celery is the real fix once it's justified by more than one service |
| The pre-send undo can't become a post-send cancel | No confirmed `cancel_booking` tool exists in Dineout's documented tool list |
| E2E tests aren't in CI | No live-stack CI service (Postgres + Redis + browser) wired up yet |
| The privacy policy is a draft | Written by the dev team, explicitly marked as needing a lawyer's review before real users rely on it |

---

## 25. Versioning

Strict [semver](https://semver.org/) from **v1.0.0** (2026-09-14) on: a breaking API/schema
change bumps major, a backward-compatible feature bumps minor, a fix bumps patch. Before
1.0.0 the project stayed in `0.x` (semver's own "anything may change" range) even through
what would now count as breaking changes, like the Swiggy-OAuth-only auth rewrite — 1.0.0
was cut deliberately once the app was genuinely production-stable (real auth, real
migrations, test coverage) rather than at an arbitrary feature milestone. Every version bump
rolls `CHANGELOG.md`'s `[Unreleased]` section into a dated entry as part of the PR that
earns it — see `CHANGELOG.md` for the full history back to `0.1.0`.

---

## 26. Roadmap

### Recently shipped

- Swiggy OAuth as the sole login (`0.9.0` → `1.0.0`) — one sign-in, no separate account
- Proactive Swiggy-reconnect nudge, structured JSON logging, PostHog analytics
- Full Playwright E2E suite against a live local stack
- Stale Next.js app deleted down to a minimal OAuth-callback shell
- Plan history UI
- Real Dineout slots surfaced in the picker and plan (`1.1.0`)
- **Dineout table booking — `book_table` + retry logic, confirmation + pre-send undo
  (`1.2.0`, this release)**

### Next — see [TODO.md](TODO.md) for the full prioritised list

- Food/Instamart item pickers (prerequisite for ordering either service)
- A lawyer review of `privacy.html`
- `create_address` for cities with no saved Swiggy address
- Phase 3 — group consensus mode, a Slack/Teams bot, corporate billing

---

## 27. Docs

- **[CHANGELOG.md](CHANGELOG.md)** — every released change, chronological
- **[TODO.md](TODO.md)** — the full prioritised backlog
- **[docs/api.md](docs/api.md)** — complete endpoint reference (request/response shapes, error codes)
- **[docs/deployment.md](docs/deployment.md)** — Railway, Vercel, migrations, Swiggy OAuth redirect URIs
- **[docs/mcp-integration.md](docs/mcp-integration.md)** — Swiggy MCP transport, address resolution, error taxonomy, the Dineout booking retry contract
- **[CLAUDE.md](CLAUDE.md)** — architecture guidance for Claude Code sessions working in this repo
