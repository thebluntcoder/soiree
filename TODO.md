# Soirée — TODO

Living backlog. `[CHANGELOG.md](CHANGELOG.md)` records what shipped; this
records what's next. Roughly ordered by priority within each section.

---

## 0. Ship what's merged ✅

- [x] Backend on Railway (`main`), frontend on Vercel — PRs #1–#5 live
- [x] Prod smoke test — new endpoints, refine, OAuth return, `location_warning`

---

## 1. Location & address resolution ✅ (except create_address)

- [x] **Tested with a real multi-city Swiggy account** — typed city
      resolves to the right saved address; fallback banner shows otherwise
- [x] **Better matching** — shared-city-word + ~35 renamed-city synonyms +
      ~90 neighbourhood → city mappings (Koramangala/Bandra/Hazratganj/…),
      both the typed side and the saved-address side expanded, so
      "Whitefield" matches a "Bengaluru" address and vice versa
- [x] **Surface the address used** — `address_used` in `/search/` response
      and the plan prompt; shown in the picker banner and above the plan
- [ ] `create_address` flow — **deferred**. When the user types a city
      with no saved address, geocode it and offer to add a temp address to
      their Swiggy account. Needs the Swiggy MCP `create_address` tool
      schema (only named in a docstring) + a consent step + testing
      against a real account (it writes to the user's Swiggy account).
- [ ] True geocoding fallback — **deferred alongside create_address**.
      Geocode the typed location + each saved address, pick nearest.
      Server-side Nominatim is rate-limited/fragile from Railway; a bundled
      city+area gazetteer covers most of it and is what the matcher does
      today. Revisit if the neighbourhood map proves too narrow.

## 2. Dineout result quality (handoff task #1)

- [x] **Parse the real MCP responses.** Confirmed against live `_debug`:
      Food packs a **JSON blob** in its text field (rich — cuisines, cost,
      distance, delivery ETA, offer, image, veg); Dineout sends **numbered
      text lines** that are sparse (name + rating + locality only) plus a
      "Search coordinates" line. `parse_mcp.py` handles both and normalises
      to the mock's `{"data": {"restaurants": [...]}}` shape. Before this
      the picker was empty with a real token and the two-step flow silently
      degraded to plan generation.
- [x] Rank + cap (rating → MCP order → distance, top 8); drop 0★ / unrated
      Dineout entries; strip "(Ad)"; city-agnostic selection rules.
- [x] Picker cards enriched from the Food JSON (image, veg dot, cuisine,
      delivery range) and null-safe for the sparse Dineout rows.
- [x] `GET /search/_debug` — now also returns `get_restaurant_details` raw
      for the top Dineout hit; `dineout_coordinates` on the `/search/`
      response for downstream enrichment.
- [x] **Picker refine + pagination.** `/search/` takes `refine` (free text —
      overrides the derived query and boosts name/cuisine/locality matches)
      and `offset`. The picker has a refine box ("Not quite right? try
      'rooftop', 'Italian', a name…") and a "Show more options" button per
      section (shown when Swiggy reports `hasMore`).
- [ ] **Enrich the selected Dineout restaurant in `/plans/generate`** —
      the Dineout list has no cuisine / ambience / slots, so `[DINEOUT]`
      plans are thin. Call `get_restaurant_details(id, lat, lng)` for the
      picked restaurant and fold cuisine / cost / ambience / slots into the
      prompt. Needs the `restaurant_details` response format (next `_debug`).

## 3. Production readiness (before any public launch)

### Security
- [ ] `SECRET_KEY` — refuse to start in `APP_ENV=production` if it's the
      default `"change-me-in-production"`
- [ ] Encrypt Swiggy access tokens at rest in Redis (currently plaintext
      JSON under `swiggy_token:{session_id}`)
- [ ] Rate limiting on `/plans/generate` and `/plans/refine` — each is
      1–2 Claude calls; an unauthenticated loop runs up the Anthropic bill
- [ ] Gate `/docs` + `/redoc` behind auth (or disable) in production
- [ ] Remove the `demo-user-001` bypass once real auth exists

### Auth (also Phase 2)
- [ ] Phone-OTP auth for Soirée itself (MSG91) — replace the single
      hardcoded demo user with real `users` rows
- [ ] Link a `session_id` / user to their generated events & plans

### Legal / privacy
- [ ] Privacy policy + explicit consent screen before Swiggy OAuth
- [ ] Data-retention & deletion policy (India DPDP Act 2023; GDPR if any
      EU users) — storing an OAuth token for a food-delivery account is
      sensitive-data processing
- [ ] "Disconnect & delete my data" that actually purges Redis + rows

### Observability / cost
- [ ] Product analytics (PostHog — OSS, self/EU-hostable): funnel
      `swiggy_auth_started → completed / failed`, plus `search`,
      `plan_generated`, `plan_refined`, `chat_message`, `approve_clicked`
      with properties (city, event_type, venue_mode, budget, guest_count,
      had_swiggy_token, latency_ms)
- [ ] Log Anthropic token usage per request → cost-per-plan dashboard
- [ ] Structured logging (JSON) instead of `logger.info` free-text

## 4. Phase 2 — Approve & Order

- [ ] `book_table` (Dineout) — needs `slotId` from `get_available_slots`;
      free bookings only in v1
- [ ] `place_food_order` (Food) — ₹1000 hard cap, COD only, **not
      idempotent** (on 5xx call `get_food_orders` before retrying)
- [ ] `checkout` (Instamart) — `spinId` required, **not idempotent**
- [ ] Mandatory confirmation screen before any order fires
- [ ] 60-second undo window (Swiggy cancel API)
- [ ] `workers/tasks.py` — Celery app (broker = `REDIS_URL`) +
      `place_all_orders(plan_id)` that writes booking/order IDs back onto
      the `plans` row and advances `PlanStatus`
- [ ] `POST /plans/{plan_id}/order` — currently returns 501
- [ ] Wire `GET /orders/{plan_id}` into a tracking UI
- [ ] Offer re-validation at checkout (offers are only fetched at
      generation with a 5-min Redis TTL)

## 5. Frontend / UX

- [ ] **Stale Next.js app (`frontend/src/`)** — decide: bring it to parity
      with `demo.html` (OAuth, two-step picker, `/plans/refine`) or delete
      it. It currently has none of those and isn't deployed
- [ ] Plan history UI — `GET /plans/history` exists, no screen for it
- [ ] Shareable plan card (+ guest RSVP — Phase 2)
- [ ] `demo.html` cost-breakdown parsing is regex-based and tolerant but
      still model-format-dependent; a structured `[COST]` block from the
      model would be sturdier
- [ ] Restaurant picker: show the resolved address / city at the top

## 6. Testing

- [ ] OAuth flow tests — `services/auth/oauth.py` + the `/auth/*`
      endpoints are still uncovered
- [ ] `refine_plan` test with a mocked Anthropic client (patch classify →
      assert patch sanitising + action routing)
- [ ] `tests/integration/test_mcp.py` — a real-token contract test if a
      token is ever available in CI (likely skip-marked)
- [ ] E2E (Playwright) against `demo.html`: form → picker → plan → refine

## 7. Phase 3 — Scale

- [ ] Group consensus mode — guests submit preferences, AI finds the
      optimal menu
- [ ] Slack / Teams bot (`/soiree lunch 12 people`)
- [ ] Corporate billing + GST receipts
- [ ] Repeat-event templates, multi-city support
- [ ] User memory — learned preferences across events
- [ ] Native mobile app (React Native)

---

## Known limitations (accepted, not bugs)

- **Alembic enum divergence** — fresh DBs get native PG `ENUM` types;
  databases created by the old `create_all` have `VARCHAR`. The idempotent
  migration never touches an existing DB and Alembic's default type
  comparison doesn't flag it. Documented in the migration file.
- **Swiggy token** — 5-day lifetime, no refresh; re-run OAuth on expiry.
- **Swiggy app conflict** — keep the Swiggy app closed during MCP sessions.
- **Dineout params** — don't send `event_type` / `dietary_filters` /
  `budget_per_head` / `start_hour` to the real API; it errors on unknown
  params (they still shape the mock + the query string).
