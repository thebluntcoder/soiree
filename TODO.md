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
- [x] **Enrich the picked Dineout restaurant.** `get_restaurant_details`
      returns "Key: Value" text (cuisine, cost, address, timings, offers,
      amenities — no slots; those need a date via `get_available_slots`).
      `parse_mcp.parse_restaurant_details` normalises it. `/plans/generate`
      merges it into `selected_dineout` before the prompt (so `[DINEOUT]`
      gets amenities / real offers / timings); `GET /search/restaurant/{id}`
      + the picker expands a card with the same data when you select it.
- [ ] Slots in the picker/plan — `get_available_slots(id, date, lat, lng)`.
      Deferred to Phase 2 (booking): the plan says a time, the booking flow
      resolves it to a real `slotId`.

## 3. Production readiness (before any public launch)

### Security
- [x] `SECRET_KEY` — the app refuses to boot in `APP_ENV=production` if it's
      still the default (`main.py`).
- [x] Swiggy access tokens **encrypted at rest** in Redis — Fernet, key
      derived from `SECRET_KEY` (`core/crypto.py`); `decrypt()` passes
      pre-existing plaintext through so old sessions keep working.
- [x] **Rate limiting** — Redis fixed-window (`core/ratelimit.py`), per
      Swiggy-session / client-IP: 25 `/plans/generate`, 40 `/plans/refine`
      + `/plans/chat`, 90 `/search/` per hour. Fails open if Redis is down.
- [x] `/docs` + `/redoc` + `/openapi.json` disabled in `APP_ENV=production`.
- [x] **`demo-user-001` removed entirely** — every plan / event / search /
      chat / order endpoint now depends on `current_user` (401 without a
      valid Soirée session). See Auth below.
- [x] Rate limiter keys on the Soirée session first (`X-Soiree-Session`),
      then legacy Swiggy session, then IP.

### Auth ✅ (Swiggy OAuth is the login)

Decided from `scripts/peek_token.py`: the Swiggy MCP access token is an
HS256 JWT carrying `sub` (stable per-user UUID) + `user_id` (Swiggy's
customer id). So one Swiggy sign-in covers everything.

- [x] `GET /auth/start` is public — PKCE + `{code_verifier, state}` in Redis.
- [x] `POST /auth/callback {code, state}` — exchange code → `decode_token_identity`
      (`oauth.py`, no signature check) → get-or-create `User` by `swiggy_sub`
      → store `swiggy_token:{user.id}` → mint 30-day session →
      `{ soiree_session, user, is_new, swiggy_expires_at }`. Opaque token → 502.
- [x] `GET /auth/status` `{connected, expires_at}`; `POST /auth/logout`
      revokes token + session; `POST /users/logout` drops the session only.
- [x] Model: `users.swiggy_sub` (unique) + `swiggy_user_id`; `phone` nullable.
      Alembic `b2c3d4e5f6a7` (idempotent, batch-mode for SQLite).
- [x] Deleted `services/auth/otp.py`, `/users/otp/*`, `MSG91_*` +
      `DEV_LOGIN_PHONES`.
- [x] `demo.html` — one "Sign in with Swiggy" button; "Reconnect Swiggy"
      chip when the token lapses. Callback page mints the session.
- [x] `scripts/seed.py` (fake local user + session), `scripts/relink_user.py`
      (move pre-OAuth events/plans to the new row).
- [ ] Run `scripts/relink_user.py` once in prod after the first Swiggy
      sign-in, to carry the old `Uttkarsh` account's events/plans over.
- [ ] Silent-refresh idea — when `/auth/status` reports the token expiring
      within ~a day, prompt "Reconnect Swiggy" proactively instead of on
      the next failed MCP call.
- [ ] Wire the stale Next.js app (`frontend/src/`) to the session, or drop
      it — it 401s on every call (already flagged stale for OAuth).

### Legal / privacy ✅ draft + mechanics

- [x] Privacy policy — `frontend/public/privacy.html`, marked **draft,
      not legal advice, needs a lawyer** at top and bottom. Covers what's
      collected (Swiggy identity, event/plan inputs, Anthropic prompts),
      where it lives (Postgres, Redis), third parties (Swiggy, Anthropic),
      retention, rights, security, contact.
- [x] Consent screen — the sign-in modal requires a checked "I agree to
      the Privacy Policy" box before the Swiggy button enables;
      `/auth/start` rejects without `consent=true` server-side too.
      `users.consent_accepted_at` records it at `/auth/callback` (Alembic
      `c3d4e5f6a7b8`). Reconnecting a lapsed token pre-checks the box
      rather than asking again.
- [x] `DELETE /users/me` — purges plans, events, the Swiggy token
      (`purge_swiggy_token`), the session, and the user row itself.
      `demo.html` "Delete my data" link, double-confirmed.
- [ ] Have an actual lawyer review `privacy.html` before real users.
- [ ] Data-retention & deletion **policy document** (India DPDP Act 2023;
      GDPR if any EU users) — the mechanics exist (above); the written
      policy answering "how long do you keep X" doesn't yet.

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

- [x] Auth tests — `tests/unit/test_auth.py` covers `decode_token_identity`
      (JWT → sub/user_id, opaque → error), `/auth/callback` (new-user
      session, bad state, opaque token), session create/resolve/revoke, and
      the `current_user` gate (401 paths). Rate-limiter precedence in
      `test_security.py`. Stream heartbeat in `test_plans_stream.py`.
- [ ] `services/auth/oauth.py` PKCE + token-exchange still need a
      mocked-httpx test; an httpx ASGITransport end-to-end for `/auth/*`
      would need `aiosqlite` in requirements (no DB fixtures today).
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
