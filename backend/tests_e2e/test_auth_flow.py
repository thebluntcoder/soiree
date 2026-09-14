"""
tests_e2e/test_auth_flow.py — Swiggy connection status in the UI.

Same live stack as test_plan_flow.py (real FastAPI + Postgres + Redis,
real browser). Covers the proactive "reconnect soon" nudge — TODO §3's
silent-refresh idea: don't wait for a failed MCP call, prompt once
GET /auth/status reports the token expiring within ~a day.
"""

from playwright.sync_api import expect


def test_swiggy_expiring_soon_shows_reconnect_nudge(authed_page, expiring_swiggy_token):
    page, static_base = authed_page

    page.goto(f"{static_base}/demo.html")
    page.wait_for_selector("#loadScreen", state="hidden", timeout=10_000)

    # checkSwiggyAuth() runs on load and calls GET /auth/status, which
    # reports connected + an expires_at inside the 24h nudge window.
    expect(page.locator("#swiggyAuthBtn")).to_be_visible(timeout=10_000)
    expect(page.locator("#swiggyAuthLabel")).to_have_text("Swiggy expiring soon")

    # Clicking it opens the login modal with the proactive (not "lapsed")
    # message — see handleSwiggyAuth()'s S.swiggyConnected branch.
    page.click("#swiggyAuthBtn")
    expect(page.locator("#loginErr")).to_contain_text("expires soon")


def test_never_connected_shows_reconnect_not_expiring_label(authed_page):
    """The default E2E user (no Swiggy token at all — never connected, as
    opposed to connected-and-lapsing) still gets the reconnect chip, same
    as before this change, but with the original "Reconnect Swiggy" label
    rather than the new "Swiggy expiring soon" one — confirms the new
    proactive nudge didn't relabel the pre-existing never-connected case."""
    page, static_base = authed_page

    page.goto(f"{static_base}/demo.html")
    page.wait_for_selector("#loadScreen", state="hidden", timeout=10_000)

    expect(page.locator("#swiggyAuthBtn")).to_be_visible(timeout=10_000)
    expect(page.locator("#swiggyAuthLabel")).to_have_text("Reconnect Swiggy")
