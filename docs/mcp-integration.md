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
demo.html (X-Session-ID)
  → endpoint: get_access_token(session_id) from Redis
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

`orchestrator._parse_address_id()` pulls a single `addressId` out of the
`get_addresses` text (`(ID: …)`), preferring a line that matches the
target city, then a `[Home]` line, then the first id.

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

## Phase 2 (not built)

`book_table` (needs `slotId` from `get_available_slots`),
`place_food_order` (₹1000 cap, COD only, **not idempotent**),
`checkout` (Instamart, `spinId` required, **not idempotent**).
All three require a confirmation screen before firing.
