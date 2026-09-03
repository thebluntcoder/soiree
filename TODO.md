# Soirée — TODO

Living backlog. `[CHANGELOG.md](CHANGELOG.md)` records what shipped; this
records what's next. Roughly ordered by priority within each section.

---

## 0. Ship what's merged

Everything since PR #1 is on `main` but **not deployed**.

- [ ] Redeploy backend to Railway (`main`) — runs `alembic upgrade head` on boot
- [ ] Redeploy frontend to Vercel — `demo.html` + `/auth/callback` page
- [ ] Smoke test on prod: Connect Swiggy → land back on `/demo.html`;
      type a non-Lucknow city → correct results or the amber banner;
      chat "switch to Italian" / "lower the budget" → plan rebuilds

---

## 1. Location & address resolution

- [ ] **Test with a real multi-city Swiggy account** — confirm the typed
      city resolves to the right saved address, and the fallback banner
      shows when it doesn't
- [ ] `create_address` flow — when the user types a city they haven't
      saved, geocode it and offer to add a temp address to their Swiggy
      account (needs an explicit consent step; verify Dineout behaves with
      a brand-new address)
- [ ] Geocode-based matching as a fallback to word-overlap — geocode the
      typed location + each saved address, pick nearest. Frontend already
      uses Nominatim for the GPS button; reuse it server-side or via a
      cached geocode table
- [ ] Surface which saved address was used, in the plan UI (not just the
      warning) so the user can tell at a glance

## 2. Dineout result quality (handoff task #1)

- [ ] Verify the restaurant-selection criteria in `build_system_prompt()`
      actually work with real Dineout data — Claude should pick a
      high-rated restaurant in a known locality that fits the occasion,
      not a random one from the 39
- [ ] Consider passing `get_restaurant_details` for the top 2–3 candidates
      so Claude has amenities / ambience / photos to choose on

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
