# Soirée API

Base URL: `/api/v1` · Interactive docs: `GET /docs` (dev only) · Health: `GET /health`

**Every endpoint below except `GET /offers/` and `GET|POST /auth/start|callback`
requires a logged-in user.** Sign in with Swiggy (see *Auth*), then send the
returned `soiree_session` as **`X-Soiree-Session`** on every request. Missing
/ invalid token → `401 {"detail": {"code": "NOT_LOGGED_IN", "message": "…"}}`.

Once logged in, if the Swiggy token is still live, plan/search calls use live
Swiggy MCP data; otherwise every MCP call returns mock data with the same
shape.

---

## Auth — Swiggy OAuth is the login

There is no separate account. Signing in = authorising Soirée against a
Swiggy account; the MCP access token (a JWT) identifies the user via its
`sub` claim.

| Method | Path | Auth | Notes |
|---|---|---|---|
| `GET`  | `/auth/start` | public | → `{ authorize_url, state }`. PKCE + state cached 2 min. Redirect the user to `authorize_url`. Rate-limited 30/hr per IP. |
| `POST` | `/auth/callback` | public | Body `{ code, state }`. Exchanges the code, reads the token's `sub`, get-or-creates the `User`, stores the encrypted token (`swiggy_token:{user_id}`, 5-day), mints a 30-day session. → `{ soiree_session, user, is_new, swiggy_expires_at }`. `400` bad/expired state, `502` if the token isn't a readable JWT. |
| `GET`  | `/auth/status` | session | → `{ connected: bool, expires_at }` — is the Swiggy token still live? |
| `POST` | `/auth/logout` | session | Revokes the Swiggy token **and** the session. |
| `GET`  | `/users/me` | session | The current user. |
| `POST` | `/users/logout` | session | Drops the session only (Swiggy token left to expire). |

The Swiggy token lasts 5 days, no refresh. When it lapses the 30-day session
still works but MCP calls fail — `/auth/status` returns `connected: false`
and the client sends the user back through `/auth/start` (one tap if Swiggy
still has them logged in).

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
