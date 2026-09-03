# Soirée — Life Events Concierge

> AI-powered event planning built on Swiggy's MCP platform (Food · Instamart · Dineout).
> Plan a date night, house party, corporate dinner, or birthday — then approve and let the agent place every order.

---

## What is Soirée?

Soirée is a full-stack AI concierge that orchestrates complete evening experiences using Swiggy's three live MCP APIs. You describe your event — who, what, where, budget — and the AI generates a complete plan: restaurant booking, food delivery picks, and grocery cart, stitched into a minute-by-minute evening timeline.

Once you approve the plan, the order agent calls Swiggy's APIs autonomously — Dineout reservation, food delivery, and Instamart cart — in a single flow.

---

## The Core Insight

Swiggy now exposes all three sides of an evening:

| MCP Server | What it enables |
|---|---|
| **Dineout** | Search restaurants, check slot availability, book a table |
| **Food** | Search restaurants, browse menus, manage cart, place delivery orders |
| **Instamart** | Search grocery products, manage cart, checkout, track delivery |

No one has built the **full-evening arc** before: start at a restaurant (Dineout), continue at home with delivery + groceries (Food + Instamart). Soirée orchestrates all three in one plan.

---

## Current Status

| Layer | Status |
|---|---|
| FastAPI backend | ✅ Working |
| SQLModel DB models (User, Event, Plan) | ✅ Working |
| Swiggy MCP clients (Food, Instamart, Dineout) | ✅ Live via user OAuth token; mock fallback with no token |
| Swiggy OAuth 2.1 PKCE (phone + OTP) | ✅ Working |
| MCP Orchestrator (asyncio.gather) | ✅ Working |
| Offer engine (Redis cache) | ✅ Working |
| Claude streaming plan generation | ✅ Working |
| Two-step flow (restaurant picker → plan) | ✅ Working |
| Events CRUD API | ✅ Working |
| Follow-up chat | ✅ Working |
| Plan persistence to DB | ✅ Working |
| Alembic migrations (real DDL, no create_all) | ✅ Working |
| `demo.html` UI (primary) | ✅ Working |
| Next.js frontend (`src/`) | ⚠️ Stale — pre-dates OAuth / two-step flow |
| Phone OTP auth for Soirée itself | 🔜 Phase 2 |
| Agentic ordering | 🔜 Phase 2 |

---

## Features

### Phase 1 — Plan & Present ✅ Built
- **Event setup** — 6 occasion types: Date / Friends Night / Birthday / Corporate / House Party / Family Dinner
- **Guest roster** — named guests with per-person dietary tags, or headcount-only mode
- **Location picker** — GPS detect or city/area text search
- **Venue mode** — Dine Out (Dineout only) / Stay In (Food + Instamart) / Hybrid (full arc)
- **Dietary + health context** — per-guest dietary flags (Veg, Vegan, Keto, Jain, Gluten-Free, Halal, No-Nuts) + wellness slider
- **AI plan generation** — Claude claude-sonnet-4-20250514 streams a complete plan via SSE: timeline, Dineout pick + slot, food delivery options, Instamart cart
- **Offer & discount engine** — live Swiggy offers fetched, filtered by budget, displayed with savings
- **Events persistence** — events saved to Postgres with full CRUD API
- **Plan persistence** — generated plans saved to Postgres with status tracking (generating → ready)
- **Follow-up chat** — conversational plan refinement after generation. Suggestion chips for common requests. Full conversation history sent to Claude for context-aware replies.

### Phase 2 — Approve & Order 🔜
- One-tap autonomous ordering — agent calls `place_food_order` + `checkout` + `book_table`
- Live order tracking across all 3 Swiggy services
- Shareable plan card with guest RSVP
- Phone OTP auth (MSG91)
- User memory — learned preferences across events
- Native mobile app (React Native)

### Phase 3 — Scale 🔜
- Group consensus mode — guests submit preferences, AI finds optimal menu
- Slack / Teams bot (`/soiree lunch 12 people`)
- Corporate billing + GST receipts
- Multi-city support, repeat event templates

---

## Architecture

### Data Flow

```
User input (event config)
        │
        ▼
  FastAPI endpoint — Pydantic validates PlanRequest
        │
        ▼
  asyncio.gather() — parallel execution
  ├── MCPOrchestrator.gather_context()
  │     ├── FoodMCPClient.search_restaurants()
  │     ├── InstamartMCPClient.search_products()
  │     └── DineoutMCPClient.search_restaurants()
  └── OffersEngine.get_active_offers() [Redis cached, 5min TTL]
        │
        ▼
  build_user_prompt() — MCP JSON injected into Claude prompt
        │
        ▼
  Claude claude-sonnet-4-20250514 streams response
  [BRIEF] [TIMELINE] [DINEOUT] [FOOD] [INSTAMART] [HEALTH] [OFFERS] [COST]
        │
        ▼
  SSE stream → Next.js frontend (ReadableStream reader)
        │
        ▼
  parsePlan() — section markers extracted into structured cards
        │
        ▼
  Rendered: Timeline card, Dineout card, Food card, Instamart card,
            Offers card, Cost card
```

### Key Technical Decisions

**1. `asyncio.gather()` for parallel MCP calls**
All three Swiggy MCP servers fire simultaneously. Serial calls would cost 3× the latency. With gather(), a hybrid event completes in the time of the slowest single call (~300ms vs ~750ms serial).

**2. SSE streaming with `⏎` proxy encoding**
Claude streams token-by-token. Newlines in the stream were getting dropped by the SSE framing protocol — section markers like `[TIMELINE]` were arriving on empty frames without `data:` prefix and getting lost. Fix: newlines are encoded as `⏎` on the backend before SSE framing, decoded back to `\n` on the frontend after reassembly.

**3. Collect-then-send instead of token streaming**
After testing, token-by-token SSE caused section markers to be split across frames unpredictably. The backend now collects the full Claude response, encodes newlines, and sends as a single SSE message. Trade-off: ~5 second wait, then instant full render.

**4. AI handles reasoning, MCP handles data**
All restaurant names, prices, slots, and products come from live Swiggy MCP calls. Claude handles tone, sequencing, and plan structure only. This prevents hallucinated restaurant names — a real trust-breaker.

**5. Offers fetched live, re-validated at checkout**
Offers are fetched at plan generation time with a 5-minute Redis TTL. Re-validated at checkout before any order is placed. Never cached stale.

**6. Mandatory confirmation before any autonomous order**
The order agent (Phase 2) always surfaces a confirmation screen before calling any Swiggy order API. One-step undo available for 60 seconds post-order via Swiggy cancel API.

---

## Tech Stack

### Backend (Python)

| Layer | Technology | Why |
|---|---|---|
| API server | **FastAPI** | Async-native, Pydantic built-in, SSE via StreamingResponse |
| AI | **Anthropic Python SDK** + claude-sonnet-4-20250514 | Native streaming, tool chaining |
| MCP | **mcp Python SDK** | Connects to all 3 Swiggy MCP servers |
| Database | **PostgreSQL** + **SQLModel** | SQLAlchemy + Pydantic fused — no schema duplication |
| Migrations | **Alembic** | Standard SQLAlchemy migration tool |
| Cache | **Redis** | Offer TTLs (5min), session storage, Celery broker |
| Background jobs | **Celery** | Async order placement (Phase 2) |
| Auth | **FastAPI-Users** + **MSG91 OTP** | Phone OTP — India-first (Phase 2) |
| Testing | **pytest** + **pytest-asyncio** + **respx** | Mock MCP responses in CI |

### Frontend

| Layer | Technology |
|---|---|
| Web | Next.js 14 (App Router) |
| Styling | Tailwind CSS |
| Fonts | Cormorant Garamond (display) + DM Sans (body) |
| State | React hooks (useState, useRef, custom usePlanStream) |
| Streaming | Native fetch + ReadableStream |

### Infrastructure

| Layer | Technology |
|---|---|
| Local dev | Docker (soiree-postgres + soiree-redis containers) |
| Python env | Conda (soiree env, Python 3.12) |
| API hosting | Railway (planned) |
| Frontend hosting | Vercel (planned) |
| CI/CD | GitHub Actions (pytest on push) |

---

## Project Structure

```
soiree/
├── backend/
│   ├── app/
│   │   ├── main.py                        # FastAPI app, lifespan, CORS
│   │   ├── core/
│   │   │   ├── config.py                  # Pydantic settings (all env vars)
│   │   │   ├── database.py                # Async SQLAlchemy engine, session, init_db
│   │   │   └── redis.py                   # Async Redis singleton client
│   │   ├── models/                        # SQLModel DB tables
│   │   │   ├── user.py                    # User (phone auth, preferences)
│   │   │   ├── event.py                   # Event (occasion, venue, guests, budget)
│   │   │   └── plan.py                    # Plan (MCP snapshots, timeline, costs, order IDs)
│   │   ├── schemas/                       # Pydantic request/response shapes
│   │   │   ├── event.py                   # EventCreate, EventRead, EventUpdate
│   │   │   ├── plan.py                    # PlanRequest, Guest, EventType, VenueMode
│   │   │   └── plan_response.py           # PlanReadResponse — GET /plans/{id}
│   │   ├── api/v1/
│   │   │   ├── router.py                  # Mounts all endpoint routers
│   │   │   └── endpoints/
│   │   │       ├── auth.py                # Swiggy OAuth 2.1 PKCE (start/callback/status/logout)
│   │   │       ├── search.py              # POST /search/ — pre-generation restaurant discovery
│   │   │       ├── events.py              # CRUD: create, list, get, patch, delete
│   │   │       ├── plans.py               # SSE streaming plan generation + chat
│   │   │       ├── users.py               # GET /users/me (demo user; real auth = Phase 2)
│   │   │       ├── offers.py              # GET /offers/ — live offers for a location + budget
│   │   │       └── orders.py              # GET /orders/{plan_id} — order status (read-only)
│   │   ├── lib/
│   │   │   └── parse_plan.py              # Server-side plan parser (mirrors frontend parsePlan.ts)
│   │   ├── services/
│   │   │   ├── plan_service.py            # DB operations: create_plan, update_plan_text, get_plan
│   │   │   ├── auth/oauth.py              # PKCE, DCR, token exchange
│   │   │   ├── mcp/
│   │   │   │   ├── base.py                # BaseMCPClient — shared JSON-RPC transport + auth
│   │   │   │   ├── orchestrator.py        # asyncio.gather() across all 3 MCPs
│   │   │   │   ├── food.py                # Swiggy Food MCP client + mocks
│   │   │   │   ├── instamart.py           # Swiggy Instamart MCP client + mocks
│   │   │   │   └── dineout.py             # Swiggy Dineout MCP client + mocks
│   │   │   ├── ai/
│   │   │   │   ├── planner.py             # Claude plan generator (collect + SSE encode)
│   │   │   │   └── prompts.py             # System + user prompt builders
│   │   │   └── offers/
│   │   │       └── engine.py              # Live offer fetch, Redis cache (5min TTL)
│   │   ├── workers/
│   │   │   └── tasks.py                   # Celery async tasks (Phase 2)
│   │   └── utils/
│   │       ├── location.py                # GPS detect, city normalisation
│   │       └── dietary.py                 # Dietary tag helpers
│   ├── tests/
│   │   ├── conftest.py                    # Shared fixtures
│   │   ├── unit/
│   │   │   ├── test_planner.py
│   │   │   └── test_offers.py
│   │   └── integration/
│   │       └── test_mcp.py
│   ├── alembic.ini                        # Alembic config — DB connection string
│   ├── alembic/
│   │   ├── env.py                         # Migration environment — imports all models
│   │   ├── script.py.mako                 # Migration file template
│   │   └── versions/
│   │       └── 9e26f57f8d8b_initial_*.py  # Initial schema — users, events, plans
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env.example
├── frontend/
│   ├── src/
│   │   ├── app/
│   │   │   ├── page.tsx                   # Home page (two-column layout)
│   │   │   ├── layout.tsx                 # Root layout, metadata
│   │   │   └── globals.css                # Base styles, font imports, CSS vars
│   │   ├── types/
│   │   │   └── index.ts                   # Shared TypeScript types (mirrors backend schemas)
│   │   ├── lib/
│   │   │   ├── api.ts                     # Fetch client, SSE stream consumer
│   │   │   └── parsePlan.ts               # Section marker parser, timeline extractor
│   └── hooks/
│       ├── usePlanStream.ts           # React hook: SSE stream state management
│       └── useChatStream.ts           # React hook: multi-turn chat state + history
│   │   └── components/
│   │       ├── event/
│   │       │   ├── EventForm.tsx          # Main form (occasion, venue, budget, time)
│   │       │   ├── GuestRoster.tsx        # Named guests + dietary tags, or headcount
│   │       │   └── LocationPicker.tsx     # Text input + GPS detect (Nominatim)
│           └── plan/
│               ├── PlanStream.tsx         # Main plan renderer (idle/streaming/done/error)
│               ├── TimelineCard.tsx       # Evening timeline with connector lines
│               ├── DineoutCard.tsx        # Restaurant reservation card
│               ├── FoodCard.tsx           # Food delivery options card
│               ├── InstamartCard.tsx      # Grocery cart card
│               ├── OffersCard.tsx         # Offers + total savings card
│               ├── CostCard.tsx           # Cost breakdown + total card
│               └── ChatPanel.tsx          # Follow-up chat UI with suggestion chips
│   ├── package.json
│   ├── next.config.js                     # API proxy rewrites to FastAPI
│   ├── tailwind.config.js                 # Custom fonts, colors, animations
│   ├── tsconfig.json                      # Path aliases (@/*)
│   └── postcss.config.js
├── docs/
├── scripts/
│   └── test_api.sh                        # API smoke test
├── .github/
│   └── workflows/
│       └── ci.yml                         # pytest + coverage on push
├── docker-compose.yml
├── CHANGELOG.md
├── .gitignore
└── README.md
```

---

## Local Development Setup

### Prerequisites
- Python 3.12 (via Conda)
- Docker Desktop
- Node.js 20+

### 1. Clone and configure

```bash
git clone https://github.com/YOUR_USERNAME/soiree.git
cd soiree
cp backend/.env.example backend/.env
# Fill in ANTHROPIC_API_KEY in backend/.env
```

### 2. Start Docker services

```bash
docker run -d --name soiree-postgres \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=soiree \
  -p 5432:5432 postgres:16-alpine

docker run -d --name soiree-redis \
  -p 6379:6379 redis:7-alpine
```

### 3. Apply database migrations

```bash
cd backend
alembic upgrade head
```

> Note: Run this once after first setup, and again whenever you pull changes that include new migration files in `alembic/versions/`.

### 4. Start backend

```bash
cd backend
conda activate soiree
uvicorn app.main:app --reload
# API running at http://localhost:8000
# Docs at http://localhost:8000/docs
```

### 5. Start frontend

```bash
cd frontend
npm install
npm run dev
# UI running at http://localhost:3000
```

### 6. Verify

```bash
curl http://localhost:8000/health
# {"status": "ok", "service": "soiree-api"}
```

---

## Debugging Guide

| Symptom | Layer | Fix |
|---|---|---|
| App won't start | Config / DB | Check `.env` exists, Docker containers running |
| `422 Unprocessable Entity` | Schema validation | Read error detail — Pydantic tells you which field |
| `404 Not Found` | Routing | Check `router.py` — endpoint mounted? |
| Data not saving | Database | Did you `await session.commit()`? Model imported in `init_db()`? |
| Plan content wrong | MCP mock data | Check `_mock_search_restaurants()` in `food.py` |
| Plan not streaming | Planner / SSE | Check `max_tokens`, SSE headers, `⏎` encoding |
| `MissingGreenlet` error | SQLAlchemy async | Using sync SQLAlchemy code in async context |
| Tables not created / `UndefinedTable` | Alembic | Run `alembic upgrade head` — `init_db()` no longer creates tables |
| New model field not appearing in DB | Alembic | Run `alembic revision --autogenerate -m "description"` then `alembic upgrade head` |

---

## Swiggy MCP access

There is **no static API key**. Each user authorises Soirée through
Swiggy's OAuth 2.1 PKCE flow (phone + OTP); the resulting access token is
stored in Redis and threaded into every MCP call as a Bearer token.

- A request that carries a valid `X-Session-ID` → **live** Swiggy data.
- A request with no session → **mock** data (identical response shape).

The switch is per-call, in `BaseMCPClient._call_mcp` — nothing to configure.
The redirect URI (`REDIRECT_URI`) must be whitelisted with Swiggy for the
OAuth handshake to complete. See `docs/mcp-integration.md`.

---

## Built with

- [Swiggy Builders Club](https://mcp.swiggy.com/builders) — Food, Instamart, Dineout MCP APIs
- [Anthropic Claude](https://anthropic.com) — AI plan generation (claude-sonnet-4-20250514)
- [FastAPI](https://fastapi.tiangolo.com) — Async Python API server
- [SQLModel](https://sqlmodel.tiangolo.com) — Database ORM
- [Next.js](https://nextjs.org) — React frontend framework

---

## Detailed Data Flow

### Plan Generation Pipeline (step by step)

```
1. User fills EventForm in browser
   └── Selects: occasion, venue mode, location, guests, budget, time, dietary, health

2. Frontend calls POST /api/v1/plans/generate
   └── Pydantic validates PlanRequest — bad fields return 422 before code runs

3. Endpoint creates Plan record in Postgres (status=generating)
   └── Returns plan_id as first SSE message so frontend knows the ID

4. asyncio.gather() fires all relevant MCP calls IN PARALLEL:
   ├── FoodMCPClient.search_restaurants(location, dietary, budget_per_head)
   ├── InstamartMCPClient.search_products(event_type, guest_count, dietary)
   └── DineoutMCPClient.search_restaurants(location, guests, event_type, slots)
   + OffersEngine.get_active_offers(location, budget) [Redis cached, 5min TTL]

   Serial latency:   Food(300ms) + Instamart(200ms) + Dineout(250ms) = 750ms
   Parallel latency: max(300ms, 200ms, 250ms) = 300ms  ← 2.5x faster

5. MCPOrchestrator._process_results() handles failures per-service
   └── If Dineout fails, Food + Instamart still return — graceful degradation

6. Budget is split across services based on venue_mode:
   ├── out:    100% Dineout
   ├── home:   70% Food + 30% Instamart
   └── hybrid: 50% Dineout + 35% Food + 15% Instamart

7. build_system_prompt() — static, same every request
   └── Defines Claude's persona, output format with section markers,
       and critical grounding rule: never invent restaurant names

8. build_user_prompt() — dynamic, different every request
   └── Serialises all MCP JSON + event config + offers into one prompt string
       Claude treats this data as ground truth

9. Claude claude-sonnet-4-20250514 generates full response
   └── collect-then-send: full response collected, newlines encoded as ⏎,
       sent as single SSE message to avoid marker fragmentation

10. Frontend api.ts receives SSE stream via ReadableStream
    └── Buffers on \n\n boundaries, decodes ⏎ → \n

11. parsePlan.ts extracts sections using [MARKER] regex
    └── Returns ParsedPlan: {brief, timeline[], dineout, food, instamart, health, offers, cost}

12. usePlanStream hook updates React state on each chunk
    └── Components re-render progressively as sections arrive

13. After [DONE] signal, backend parse_plan.py parses same text server-side
    └── Saves to plans table: status=ready, total_cost, total_savings, all sections

14. User sees complete plan with cards: Timeline, Dineout, Food, Instamart, Offers, Cost
```

---

## Database Schema

### Table: `users`
```
id               VARCHAR  PRIMARY KEY  UUID generated in Python
phone            VARCHAR  UNIQUE       Indian mobile, e.g. +919876543210
name             VARCHAR  NULLABLE     Set after first login
email            VARCHAR  NULLABLE     For receipts and notifications
preferred_cuisines VARCHAR NULLABLE    JSON string: ["North Indian", "Italian"]
dietary_tags     VARCHAR  NULLABLE     JSON string: ["Veg", "No-Nuts"]
default_city     VARCHAR  NULLABLE     e.g. "Lucknow"
created_at       TIMESTAMP             Auto-set
updated_at       TIMESTAMP             Updated on every PATCH
is_active        BOOLEAN               Soft delete flag
```

### Table: `events`
```
id               VARCHAR  PRIMARY KEY  UUID
user_id          VARCHAR  FK→users     Owner of this event
event_type       ENUM                  date/friends/birthday/corporate/house_party/family
venue_mode       ENUM                  out/home/hybrid
status           ENUM                  draft/planned/ordered/completed/cancelled
location         VARCHAR               City or address as entered
latitude         FLOAT    NULLABLE     Resolved via geocoding
longitude        FLOAT    NULLABLE     Resolved via geocoding
event_date       TIMESTAMP NULLABLE    Date of event, None = today
start_hour       INT                   24h format, 10-23
budget           INT                   Total INR across all Swiggy services
guest_count      INT                   1-100
guests           VARCHAR  NULLABLE     JSON: [{"name": "Anjali", "dietary_tags": ["Veg"]}]
dietary_tags     VARCHAR  NULLABLE     JSON: ["Veg", "Jain"] — group level
health_focus     INT                   0=indulgent, 100=healthy
notes            VARCHAR  NULLABLE     Free text context
created_at       TIMESTAMP
updated_at       TIMESTAMP
```

### Table: `plans`
```
id               VARCHAR  PRIMARY KEY  UUID
event_id         VARCHAR  FK→events    Parent event
user_id          VARCHAR  FK→users     Denormalised for fast user queries
status           ENUM                  generating/ready/approved/ordering/confirmed/failed
timeline         VARCHAR  NULLABLE     JSON: [{time, emoji, title, detail}]
dineout_options  VARCHAR  NULLABLE     Dineout MCP result text
food_options     VARCHAR  NULLABLE     Food MCP result text
instamart_cart   VARCHAR  NULLABLE     Instamart MCP result text
active_offers    VARCHAR  NULLABLE     Offers text shown to user
health_insight   VARCHAR  NULLABLE     AI dietary note
dineout_cost     INT      NULLABLE     INR
food_cost        INT      NULLABLE     INR
instamart_cost   INT      NULLABLE     INR
total_cost       INT      NULLABLE     Sum of all services INR
total_savings    INT      NULLABLE     Total saved via offers INR
dineout_booking_id VARCHAR NULLABLE   Phase 2: Dineout confirmation
food_order_id    VARCHAR  NULLABLE     Phase 2: Food order confirmation
instamart_order_id VARCHAR NULLABLE   Phase 2: Instamart order confirmation
edit_count       INT                   How many times user edited via chat
last_edited_at   TIMESTAMP NULLABLE
created_at       TIMESTAMP
approved_at      TIMESTAMP NULLABLE   When user clicked "Confirm Plan"
```

### Relationships
```
users (1) ──── (many) events
events (1) ──── (many) plans   ← user can regenerate multiple times per event
```

---

## Key Concepts Explained

### async / await — why everything is non-blocking
Python's `async/await` means the server doesn't freeze while waiting for a DB query or API call. It handles other requests in the meantime. This is critical because a plan generation request involves 3 MCP calls + 1 offers fetch + 1 Claude call — all network operations that take hundreds of milliseconds each.

```python
# WITHOUT async — server freezes for 750ms per request
food = food_client.search()       # wait 300ms
instamart = instamart_client.search()  # wait 200ms
dineout = dineout_client.search()  # wait 250ms

# WITH async + gather — all 3 fire simultaneously, done in 300ms
food, instamart, dineout = await asyncio.gather(
    food_client.search(),
    instamart_client.search(),
    dineout_client.search(),
)
```

### Dependency Injection — how sessions reach endpoints
FastAPI automatically creates and injects a DB session into any endpoint that declares `Depends(get_session)`. You never manually open or close sessions — the context manager handles it.

```python
@router.post("/events/")
async def create_event(
    payload: EventCreate,
    session: AsyncSession = Depends(get_session),  # injected automatically
):
    ...
```

### SSE Streaming — why plans appear token by token
Server-Sent Events is a browser standard for one-way server→browser streams. The backend yields `data: <content>\n\n` chunks. The browser's `EventSource` or `ReadableStream` receives each chunk as it arrives.

**The `⏎` encoding problem and solution:**
Claude's output contains newlines. When sent over SSE, a bare newline `\n` is interpreted as an SSE message separator — causing section markers like `[TIMELINE]` to arrive on empty frames and get dropped. Solution: encode all `\n` as `⏎` on the backend before SSE framing, decode back on the frontend after reassembly.

### Mock-first MCP — how the live/mock switch works
Every MCP call goes through `BaseMCPClient._call_mcp(tool, params, access_token)`:
```python
if not access_token:
    return await self._mock_dispatch(tool_name, params)   # mock
return await self._real_mcp_call(tool_name, params, access_token)  # live
```
No token (no Swiggy session) → mock data with the exact same shape as real
responses. Token present → real JSON-RPC call. The decision is per-request,
so the same deployment serves both mock demos and live users.

### Schemas vs Models — why they're separate
**Models** (`models/`) define DB tables — what goes into Postgres.
**Schemas** (`schemas/`) define API shapes — what the client sends and receives.

They're separate because:
- DB models have internal fields the API should never expose (raw FK IDs)
- API schemas have validation rules that don't belong in DB models
- You often want different fields for create vs read vs update operations

### PlanStatus State Machine
```
generating → ready → approved → ordering → confirmed
                              ↘ failed
```
Each transition is triggered by a specific action:
- `generating`: set when Claude streaming starts
- `ready`: set when streaming + DB save completes
- `approved`: set when user clicks "Confirm Plan" (Phase 2)
- `ordering`: set when order agent begins calling Swiggy APIs (Phase 2)
- `confirmed`: set when all order IDs received (Phase 2)
- `failed`: set if any Swiggy order call fails (Phase 2)

---

## Database Migration Workflow

Alembic manages all schema changes. Never edit the DB manually or use `create_all()`.

### First time setup
```bash
cd backend
alembic upgrade head    # applies all migrations to your DB
```

### Adding a new field to a model
```bash
# 1. Edit the model file e.g. models/user.py — add the new field
# 2. Generate migration — Alembic diffs models vs live DB
alembic revision --autogenerate -m "add default_city to users"
# 3. Review the generated file in alembic/versions/ — check it looks right
# 4. Apply it
alembic upgrade head
```

### Other useful commands
```bash
alembic current          # show current migration version in DB
alembic history          # list all migrations
alembic downgrade -1     # roll back one migration
alembic downgrade base   # roll back everything (careful — data loss)
alembic upgrade head --sql  # preview SQL without applying (dry run)
```

### Rule
Every schema change goes through Alembic. If you add a field to a model
and don't run a migration, the column won't exist in Postgres and you'll
get an `UndefinedColumn` error at runtime.

---

## Debugging Flowchart

```
Bug appears
    │
    ├── App won't start
    │     ├── Check: does backend/.env exist?
    │     ├── Check: are Docker containers running? (docker ps)
    │     └── Check: is ANTHROPIC_API_KEY set in .env?
    │
    ├── 422 Unprocessable Entity
    │     └── Pydantic validation failed — read the error detail,
    │         it tells you exactly which field and why
    │
    ├── 404 Not Found
    │     └── Check router.py — is the endpoint mounted?
    │         Check the URL — prefix is /api/v1/...
    │
    ├── Data not saving to DB
    │     ├── Did you await session.commit()?
    │     ├── Ran alembic upgrade head after pulling new migrations?
    │     └── Check: psql \dt — does the table exist?
    │
    ├── Plan generates but content is wrong
    │     ├── Check mock data in services/mcp/food.py _mock_search_restaurants()
    │     └── Add print(user_prompt) in planner.py to see what Claude receives
    │
    ├── Plan not streaming / 500 error
    │     ├── Check uvicorn terminal — full traceback is there
    │     ├── Check ANTHROPIC_API_KEY is valid
    │     └── Check max_tokens in planner.py (currently 2000)
    │
    ├── ⏎ symbols visible in frontend
    │     └── parsePlan.ts — check .replace(/⏎/g, '\n') is present in parsePlan()
    │
    ├── Section markers missing ([TIMELINE] not found)
    │     └── Check api.ts — buffer accumulation on \n\n boundaries
    │
    └── ForeignKeyViolationError on plan insert
          └── event_id doesn't exist in events table —
              ensure demo event is created before plan
```

---

## Risks & Mitigations

| Risk | Severity | Mitigation |
|---|---|---|
| Swiggy MCP rate limits | High | Cache restaurant/menu data per city (1hr TTL). Batch Instamart lookups. Degrade gracefully with AI-only suggestions if throttled. |
| Agentic order errors | High | Mandatory confirmation screen. 60s undo window. Dry-run mode before Phase 2 goes live. |
| Offer data staleness | Medium | Fetched live at generation. Expiry shown on every badge. Re-validated at checkout. |
| Dineout slot gone by checkout | Medium | Show 3 restaurant options with pre-fetched slots. AI auto-suggests next available time if preferred slot gone. |
| AI hallucinating restaurants | Low | All names/prices come from MCP data. Claude never invents food data. Enforced in system prompt. |
| SSE marker fragmentation | Low | Solved via ⏎ proxy encoding — newlines encoded before SSE framing, decoded after reassembly. |
| Schema drift frontend↔backend | Low | TypeScript types in types/index.ts mirror Pydantic schemas exactly. Update both when changing either. |

---

## Versioning Strategy

| Version | Meaning |
|---|---|
| `0.x.0` | Phase 1 milestones |
| `0.x.x` | Incremental additions within a phase |
| `1.0.0` | Phase 1 complete — plan & present fully working |
| `2.0.0` | Phase 2 complete — agentic ordering |
| `3.0.0` | Phase 3 complete — corporate + social scale |

---

## Roadmap

- [x] Project scaffold — directory structure, README, CI
- [x] Core foundation — database, Redis, models (User, Event, Plan)
- [x] Swiggy MCP clients — Food, Instamart, Dineout (mock mode)
- [x] MCP Orchestrator — asyncio.gather() parallel coordinator
- [x] Offer engine — live fetch, Redis cache
- [x] AI planner — Claude streaming, SSE encoding
- [x] Events CRUD API
- [x] Plan persistence — save to DB after generation
- [x] Next.js frontend — streaming plan UI
- [x] Follow-up chat UI — suggestion chips, streaming replies, conversation history
- [x] Alembic migrations — real DDL, `create_all()` removed
- [x] Unit + integration tests — 66 passing, CI on push/PR
- [x] Real Swiggy MCP — live via per-user OAuth 2.1 PKCE token
- [x] Two-step flow — restaurant picker feeds the chosen venue into the plan
- [ ] Phone OTP auth for Soirée itself (Phase 2)
- [ ] Agentic ordering (Phase 2)
- [ ] Shareable plan card (Phase 2)
- [ ] User memory / preference learning (Phase 2)
- [ ] Group consensus mode (Phase 3)
- [ ] Slack / Teams bot (Phase 3)
- [ ] Corporate billing + GST receipts (Phase 3)