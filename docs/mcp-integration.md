# Swiggy MCP integration

Three MCP servers, one transport. `services/mcp/base.py::BaseMCPClient`
owns the HTTP mechanics; `food.py`, `instamart.py`, `dineout.py` each just
declare their `MCP_URL` and their mock responses.

| Server | URL | Address scope |
|---|---|---|
| Food | `https://mcp.swiggy.com/food` | `addressId` from `get_addresses` |
| Instamart | `https://mcp.swiggy.com/im` | `addressId` from `get_addresses` |
| Dineout | `https://mcp.swiggy.com/dineout` | `addressId` too — it resolves lat/lng itself |

## Real vs mock

Decided per call by one thing: whether an `access_token` was passed to
`_call_mcp`. Token → real JSON-RPC call. No token → `_mock_dispatch`.
There is no `use_mock` flag and no static API key.

```
demo.html (X-Soiree-Session)
  → deps.current_user → User
  → endpoint: get_access_token(user.id) from Redis  (key: swiggy_token:{user_id})
  → event_data["access_token"]
  → MCPOrchestrator.gather_context(access_token=…)
  → client._call_mcp(tool, params, access_token=…)
```

## Request shape

```http
POST {MCP_URL}
Authorization: Bearer {access_token}
Content-Type: application/json
Accept: application/json, text/event-stream      # 406 without this

{"jsonrpc":"2.0","method":"tools/call",
 "params":{"name":"<tool>","arguments":{...}},"id":1}
```

## Response shape

Swiggy returns **text**, not structured JSON:

```json
{"result":{"content":[{"type":"text","text":"Found 10 restaurants..."}]}}
```

`orchestrator._resolve_address_id()` pulls a single `addressId` out of the
`get_addresses` text (`(ID: …)`), preferring a line that matches the city
the user typed (`_city_search_pattern` handles Gurugram/Gurgaon-style
aliases), then a `[Home]` line, then the first id. It also reports whether
the city actually matched — if not, `gather_context` adds a
`location_warning` to the context so the plan and picker can tell the user
their default address was used (you can only search from saved Swiggy
addresses, so a city with no saved address can't be searched directly).

## Errors → re-auth

| HTTP | `PermissionError` code | Frontend action |
|---|---|---|
| 401 | `SWIGGY_TOKEN_EXPIRED` | re-run `/auth/start` |
| 419 | `SWIGGY_SESSION_REVOKED` | full re-auth (phone + OTP) |
| 403 | `SWIGGY_SCOPE_ERROR` | re-auth with broader scope |

## Venue mode → which servers

| mode | servers | budget split |
|---|---|---|
| `out` | Dineout | 100% dineout |
| `home` | Food + Instamart | 70 / 30 |
| `hybrid` | all three | 60 / 20 / 20 (Food is cake/dessert only) |

## Dineout search params that work

```python
{"query": "restaurant", "guestCount": 2, "addressId": "43530781"}
```

`query` must be non-empty (single word is safest). Do **not** send
`event_type`, `dietary_filters`, `budget_per_head`, `start_hour` — the
Dineout API errors on unknown params. Those args still shape the mock
response and the query string.

## Booking (Dineout — the only service actually wired up for ordering)

`book_table(restaurant_id, slot_id, guest_count, booking_date)` — NOT
idempotent, no documented idempotency-key param. `services/orders/
dineout_ordering.py::book_with_retry` is the retry contract around that:

- 401/419/403 → never retry (existing `PermissionError` taxonomy above).
- 4xx / RPC error → the request was rejected outright (e.g. stale
  `slotId`) — one corrective retry against a freshly re-fetched slot.
- 5xx / timeout → **ambiguous**: try to recover a `bookingId` from the
  failed response body first; if found, `get_booking_status(booking_id)`
  settles it (`CONFIRMED`/`PENDING` → done, `NOT_FOUND` → safe to retry).
  If no `bookingId` is recoverable, re-check the same `slotId` via
  `get_available_slots` — still available → safe to retry; gone or the
  re-check itself fails → **stop, don't retry** (can't tell if that's us
  or someone else, and retrying risks a double booking). This is a
  heuristic, not proof — see the module docstring for the full reasoning.

`parse_mcp.parse_booking` (real response format **unconfirmed** — no live
token tested yet, same caveat as `parse_available_slots`) parses both
`book_table` and `get_booking_status` responses.

Confirmation is pre-send, not post-send: there's no confirmed
`cancel_booking` tool, so "undo" is a 60s client-side delay *before*
`book_table` ever fires (demo.html), not cancellation of something
already booked.

## Phase 2, still not built

Food `place_food_order` and Instamart `checkout` need a real item/dish/
product picker first — today's picker only carries dish *names* for Food
(no IDs) and has no product-selection step for Instamart at all. That's
a separate future UX project, not a backend wire-up — see TODO.md §4.
