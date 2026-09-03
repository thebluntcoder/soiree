# Soirée API

Base URL: `/api/v1` · Interactive docs: `GET /docs` · Health: `GET /health`

All plan/search calls accept an optional `X-Session-ID` header. When present
and valid, Soirée uses the user's Swiggy OAuth token for **live** MCP data;
when absent, every MCP call returns mock data with the same shape.

---

## Auth (Swiggy OAuth 2.1 PKCE)

| Method | Path | Notes |
|---|---|---|
| `GET`  | `/auth/start` | Returns `{ authorize_url, state }`. Redirect the user to `authorize_url`. |
| `POST` | `/auth/callback` | Body `{ code, state }`. Exchanges the code, returns `{ session_id, expires_at }`. |
| `GET`  | `/auth/status?session_id=` | `{ authenticated: bool, expires_at }`. |
| `POST` | `/auth/logout?session_id=` | Revokes the token and clears the session. |

Token lifetime is 5 days, no refresh — re-run `/auth/start` on expiry.

## Search — restaurant discovery (Step 1.5)

`POST /search/` — body is a `SearchRequest` (`event_type`, `venue_mode`,
`location`, `budget`, `guest_count`, optional `guests`, `dietary_tags`,
`alcohol_preference`, `notes`, `lat`, `lng`).

Returns `{ dineout: [...], food: [...], venue_mode, budget_split }` — real
restaurant cards for the picker. No Claude call, ~300 ms.

## Plans

| Method | Path | Notes |
|---|---|---|
| `POST` | `/plans/generate` | SSE stream. Body is a `PlanRequest` (SearchRequest fields + optional `selected_dineout` / `selected_food` — the full restaurant object the user picked). First frame `data: PLAN_ID:<uuid>`, then one frame with the whole plan (newlines encoded as `⏎`), then `data: [DONE]`. Persists a `Plan` (and a fresh `Event`) to Postgres. |
| `POST` | `/plans/refine` | JSON. Body `{ user_message, event_data, plan_text, conversation_history }`. Classifies the message: `{"action":"answer","reply":"…","patch":{}}` for a question, or `{"action":"modify","reply":"…","patch":{<PlanRequest field overrides>}}` for a change. On `modify` the client merges `patch` into the request and re-runs `/plans/generate`. `patch` is sanitised server-side (only `notes`/`budget`/`guest_count`/`dietary_tags`/`alcohol_preference`/`venue_mode`/`health_focus`/`start_hour`, values clamped). |
| `POST` | `/plans/chat` | SSE stream, **advisory only** (never changes the plan). Kept for the legacy Next.js client; `demo.html` uses `/plans/refine`. |
| `GET`  | `/plans/{plan_id}` | The saved plan. |
| `GET`  | `/plans/event/{event_id}` | All plans for an event (newest first). |
| `GET`  | `/plans/history` | 20 most recent `ready` plans for the demo user. |
| `POST` | `/plans/{plan_id}/order` | **501** — Phase 2 (autonomous ordering). |

### Plan text format

Claude emits section markers the frontend parses:
`[BRIEF] [TIMELINE] [DINEOUT] [FOOD] [INSTAMART] [HEALTH] [OFFERS] [COST]`.
`[COST]` is `Dineout: ₹x | Food Delivery: ₹y | Instamart: ₹z` then
`TOTAL: ₹sum`. `parse_plan.py` extracts per-service and total costs into
integer columns on the `plans` row.

## Events

`POST /events/` · `GET /events/` · `GET /events/{id}` · `PATCH /events/{id}` ·
`DELETE /events/{id}` — standard CRUD, all attributed to the demo user.

## Offers

`GET /offers/?location=&budget=` → `{ location, budget, count, offers: [...] }`.
Redis-cached 5 min. Offers whose `min_order` exceeds `budget` are filtered out.

## Users

`GET /users/me` → the current (demo) user. Real phone-OTP auth is Phase 2.

## Orders

`GET /orders/{plan_id}` → `{ plan_id, status, placed, orders, approved_at }`.
Read-only; `orders` ids stay null until the Phase 2 ordering agent runs.
