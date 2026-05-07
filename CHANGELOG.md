# Changelog

All notable changes to Soirée will be documented here.
Format based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

### Added
- Placeholder for in-progress features and upcoming additions.

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