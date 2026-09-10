# Soirée API

Base URL: `/api/v1` · Interactive docs: `GET /docs` (dev only) · Health: `GET /health`

**Every endpoint below except `GET /offers/` and the OTP endpoints requires a
logged-in Soirée user.** Log in with phone-OTP (see *Login*), then send the
returned token as **`X-Soiree-Session`** on every request. Missing / invalid
token → `401 {"detail": {"code": "NOT_LOGGED_IN", "message": "…"}}`.

Once logged in, if the user has also connected Swiggy (see *Swiggy link*),
plan/search calls use their live Swiggy MCP data; otherwise every MCP call
returns mock data with the same shape.

---

## Login (phone OTP)

| Method | Path | Notes |
|---|---|---|
| `POST` | `/users/otp/request` | Body `{ phone }` (any Indian mobile format). Sends a 6-digit code. → `{ sent: true, phone: "+91…" }`. Rate-limited 5 / 10 min per number. |
| `POST` | `/users/otp/verify` | Body `{ phone, code, name? }`. → `{ soiree_session, user, is_new }`. Creates the user on first login. Code TTL 5 min, 5 attempts. |
| `GET`  | `/users/me` | The current user (`X-Soiree-Session`). |
| `POST` | `/users/logout` | Revokes the session. |

Sender: **MSG91** when `MSG91_AUTH_KEY` + `MSG91_TEMPLATE_ID` are set,
otherwise the code is logged server-side and — when `APP_ENV != production` —
the magic code **`000000`** always verifies. Session tokens live 30 days in
Redis.

## Swiggy link (OAuth 2.1 PKCE)

All four require `X-Soiree-Session`. The Swiggy token is stored against the
Soirée `user.id`, so one login owns one Swiggy connection.

| Method | Path | Notes |
|---|---|---|
| `GET`  | `/auth/start` | → `{ authorize_url, state }`. Redirect the user to `authorize_url`. |
| `POST` | `/auth/callback` | Body `{ code, state }`. Exchanges the code, stores the token. → `{ connected: true, expires_at }`. `403` if a different session started this `state`. |
| `GET`  | `/auth/status` | → `{ connected: bool, expires_at }`. |
| `POST` | `/auth/logout` | Revokes the Swiggy token and forgets it. |

Token lifetime is 5 days, no refresh — re-run `/auth/start` on expiry.

## Search — restaurant discovery (Step 1.5)

`POST /search/` — body is a `SearchRequest` (`event_type`, `venue_mode`,
`location`, `budget`, `guest_count`, optional `guests`, `dietary_tags`,
`alcohol_preference`, `notes`, `lat`, `lng`, `refine`, `offset`).

Returns `{ dineout: [...], food: [...], venue_mode, budget_split, refine,
offset, dineout_has_more, food_has_more, location_warning, address_used,
dineout_coordinates }` — real restaurant cards for the picker. No Claude
call, ~300 ms.

`GET /search/restaurant/{id}?lat=&lng=` — full details for one Dineout
restaurant (`{}` if Swiggy isn't connected).
`GET /search/_debug` — raw MCP text for parser tuning (needs Swiggy connected).

## Plans

| Method | Path | Notes |
|---|---|---|
| `POST` | `/plans/generate` | SSE stream. Body is a `PlanRequest` (SearchRequest fields + optional `selected_dineout` / `selected_food`). First frame `data: PLAN_ID:<uuid>`, then one frame with the whole plan (newlines encoded as `⏎`), then `data: [DONE]`. Persists a `Plan` (and a fresh `Event`) owned by the current user. |
| `POST` | `/plans/refine` | JSON. Body `{ user_message, event_data, plan_text, conversation_history }`. Classifies the message: `{"action":"answer","reply":"…","patch":{}}` for a question, or `{"action":"modify","reply":"…","patch":{<PlanRequest field overrides>}}` for a change. On `modify` the client merges `patch` into the request and re-runs `/plans/generate`. `patch` is sanitised server-side (only `notes`/`budget`/`guest_count`/`dietary_tags`/`alcohol_preference`/`venue_mode`/`health_focus`/`start_hour`, values clamped). |
| `POST` | `/plans/chat` | SSE stream, **advisory only** (never changes the plan). Kept for the legacy Next.js client; `demo.html` uses `/plans/refine`. |
| `GET`  | `/plans/{plan_id}` | The saved plan — `404` if it isn't yours. |
| `GET`  | `/plans/event/{event_id}` | All plans for one of your events (newest first). |
| `GET`  | `/plans/history` | Your 20 most recent `ready` plans. |
| `POST` | `/plans/{plan_id}/order` | **501** — Phase 2 (autonomous ordering). |

### Plan text format

Claude emits section markers the frontend parses:
`[BRIEF] [TIMELINE] [DINEOUT] [FOOD] [INSTAMART] [HEALTH] [OFFERS] [COST]`.
`[COST]` is `Dineout: ₹x | Food Delivery: ₹y | Instamart: ₹z` then
`TOTAL: ₹sum`. `parse_plan.py` extracts per-service and total costs into
integer columns on the `plans` row.

## Events

`POST /events/` · `GET /events/` · `GET /events/{id}` · `PATCH /events/{id}` ·
`DELETE /events/{id}` — standard CRUD, all scoped to the current user
(`404` on another user's event).

## Offers

`GET /offers/?location=&budget=` → `{ location, budget, count, offers: [...] }`.
Redis-cached 5 min. Offers whose `min_order` exceeds `budget` are filtered
out. **No login required** — public deal data, no Swiggy token used.

## Orders

`GET /orders/{plan_id}` → `{ plan_id, status, placed, orders, approved_at }`.
Read-only, your plans only; `orders` ids stay null until the Phase 2 ordering
agent runs.
