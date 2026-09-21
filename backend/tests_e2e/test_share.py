"""
tests_e2e/test_share.py — the shareable plan link, for real.

The owner generates a plan and turns sharing on; a *separate, anonymous*
browser context (no localStorage, no session — i.e. an actual recipient)
opens the link. Runs against the real FastAPI + Postgres stack, so this is
also where enable_sharing's conditional UPDATE and the unique index get
exercised for real (unit tests can only fake them).

The XSS test matters most: plan text is Claude's output, partly shaped by
the owner's own free-text notes, and it's inserted into innerHTML. Before
sharing existed that was self-XSS at worst; with a public link it would
run in a *stranger's* browser on the origin holding their session token.
"""

import pytest
from playwright.sync_api import expect

API = "http://localhost:8000/api/v1"

_HOSTILE_PLAN = (
    "[BRIEF]\n"
    "A date.\n"
    "[TIMELINE]\n"
    '8:00 PM | 🍽️ | Dinner <img src=x onerror="window.__pwned=1"> | Table for two\n'
    "[DINEOUT]\n"
    'Farzi Cafe <img src=x onerror="window.__pwned=1"> rooftop\n'
    "[FOOD]\n"
    'Meghana <svg onload="window.__pwned=1"> Foods\n'
    "[INSTAMART]\n"
    "Candles\n"
    "[HEALTH]\n"
    '<img src=x onerror="window.__pwned=1"> light\n'
    "[OFFERS]\n"
    '<img src=x onerror="window.__pwned=1"> 15% off\n'
    "[COST]\n"
    '{"dineout": 1800, "food": 400, "instamart": 150, "total": 2350}'
)


def _generate_plan(page, static_base):
    page.goto(f"{static_base}/demo.html")
    page.wait_for_selector("#loadScreen", state="hidden", timeout=10_000)
    page.fill("#loc", "Lucknow")
    page.click("#planCta")
    page.wait_for_selector("#pickerState", state="visible", timeout=15_000)
    page.click("#dcard-0")
    page.click("#fcard-0")
    page.click("#generateFromPicker")
    page.wait_for_selector("#planContent", state="visible", timeout=20_000)
    expect(page.locator("#planContent")).to_contain_text("Farzi Cafe")


def _open_share_modal(page) -> str:
    page.click("text=Share")
    expect(page.locator("#shareLinkRow")).to_be_visible(timeout=5_000)
    link = page.input_value("#shareLinkInput")
    assert "?share=" in link
    return link


@pytest.fixture
def recipient(browser):
    """A brand-new anonymous browser context — nothing carried over from the
    owner's page, so anything it can see, any stranger with the link can."""
    ctx = browser.new_context()
    page = ctx.new_page()
    yield ctx, page
    ctx.close()


def test_share_link_opens_a_read_only_view_for_anyone(authed_page, recipient):
    page, static_base = authed_page
    _, rpage = recipient

    _generate_plan(page, static_base)
    link = _open_share_modal(page)

    # Idempotent: sharing again hands back the same link, not a new one.
    page.click("#shareModal >> text=✕")
    assert _open_share_modal(page) == link

    rpage.goto(link)
    rpage.wait_for_selector("#loadScreen", state="hidden", timeout=10_000)
    plan = rpage.locator("#planContent")
    expect(plan).to_contain_text("Farzi Cafe", timeout=10_000)
    expect(plan).to_contain_text("2,350")
    expect(rpage.locator(".shared-banner")).to_contain_text("shared via Soirée")

    # Read-only: no form, no account UI, and nothing that acts on the plan.
    expect(rpage.locator(".sidebar")).to_be_hidden()
    expect(rpage.locator("#acctBtn")).to_be_hidden()
    expect(rpage.locator("text=Approve & Order")).to_have_count(0)
    expect(rpage.locator("#chatInp")).to_have_count(0)
    expect(rpage.locator("text=Try Soirée")).to_be_visible()
    expect(rpage.locator("#orderBanner")).to_be_hidden()


def test_public_endpoint_exposes_only_the_allowlist(authed_page, recipient):
    page, static_base = authed_page
    ctx, _ = recipient

    _generate_plan(page, static_base)
    token = _open_share_modal(page).split("?share=")[1]

    resp = ctx.request.get(f"{API}/shared/{token}")  # no session header
    assert resp.status == 200
    assert resp.headers["cache-control"] == "no-store"
    body = resp.json()
    for private in ("id", "user_id", "event_id", "status", "share_token",
                    "dineout_selection", "dineout_booking_id", "order_error"):
        assert private not in body, f"{private} leaked into the public response"
    assert body["total_cost"] == 2350


def test_stop_sharing_kills_the_link_immediately(authed_page, recipient):
    page, static_base = authed_page
    ctx, rpage = recipient

    _generate_plan(page, static_base)
    link = _open_share_modal(page)
    token = link.split("?share=")[1]
    assert ctx.request.get(f"{API}/shared/{token}").status == 200

    page.click("text=Stop sharing")
    expect(page.locator("#shareBody")).to_contain_text("Sharing is off")

    assert ctx.request.get(f"{API}/shared/{token}").status == 404
    rpage.goto(link)
    rpage.wait_for_selector("#loadScreen", state="hidden", timeout=10_000)
    expect(rpage.locator("#planContent")).to_contain_text("Plan not available", timeout=10_000)
    expect(rpage.locator("#planContent")).not_to_contain_text("Farzi Cafe")


def test_unknown_token_shows_not_available(recipient):
    _, rpage = recipient
    rpage.goto("http://localhost:3000/demo.html?share=not-a-real-token")
    rpage.wait_for_selector("#loadScreen", state="hidden", timeout=10_000)
    expect(rpage.locator("#planContent")).to_contain_text("Plan not available", timeout=10_000)


def test_anonymous_caller_cannot_share_or_unshare(authed_page, recipient):
    """Knowing a plan ID isn't enough to toggle sharing without a session.
    (A *different logged-in user* getting 404 is covered in tests/unit/
    test_share.py — the E2E fixture only seeds one user.)"""
    page, static_base = authed_page
    ctx, _ = recipient

    _generate_plan(page, static_base)
    plan_id = page.evaluate("S.planId")

    # Anonymous → 401, not a share token.
    assert ctx.request.post(f"{API}/plans/{plan_id}/share").status == 401
    assert ctx.request.delete(f"{API}/plans/{plan_id}/share").status == 401


def test_shared_plan_cannot_run_html_injected_into_it(
    authed_page, recipient, plan_text_override
):
    """Stored-XSS regression. The plan text carries live <img onerror> / <svg
    onload> payloads in every persisted section; neither the owner's own view
    nor a recipient's may execute them — they must render as inert text."""
    page, static_base = authed_page
    _, rpage = recipient
    plan_text_override(_HOSTILE_PLAN)

    _generate_plan(page, static_base)
    page.wait_for_timeout(500)
    assert page.evaluate("window.__pwned") is None
    link = _open_share_modal(page)

    rpage.goto(link)
    rpage.wait_for_selector("#loadScreen", state="hidden", timeout=10_000)
    plan = rpage.locator("#planContent")
    expect(plan).to_contain_text("Farzi Cafe", timeout=10_000)
    rpage.wait_for_timeout(500)  # an onerror would have fired by now

    assert rpage.evaluate("window.__pwned") is None
    expect(rpage.locator("#planContent img, #planContent svg")).to_have_count(0)
    expect(rpage.locator("#planContent [onerror], #planContent [onload]")).to_have_count(0)
    # …and the payload is visible as literal text rather than swallowed.
    expect(plan).to_contain_text("<img src=x")
