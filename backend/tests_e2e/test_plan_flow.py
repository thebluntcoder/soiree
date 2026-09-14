"""
tests_e2e/test_plan_flow.py — the golden path through demo.html, for real.

form → picker → plan → refine, driven by a real browser against a real
FastAPI + Postgres + Redis stack (Claude is the one mocked piece — see
conftest.py's `_mock_claude` for why). No Swiggy token is connected, so
the MCP layer serves its own built-in mock restaurant data — this is
exactly the demo/no-token experience a first-time visitor gets.
"""

from playwright.sync_api import expect


def test_form_to_picker_to_plan_to_refine(authed_page):
    page, static_base = authed_page

    page.goto(f"{static_base}/demo.html")
    page.wait_for_selector("#loadScreen", state="hidden", timeout=10_000)

    # Session from localStorage was picked up — not shown the sign-in prompt.
    expect(page.locator("#acctLabel")).not_to_have_text("Sign in")

    # ── Step 1: form ────────────────────────────────────────────────────
    page.fill("#loc", "Lucknow")
    page.click("#planCta")

    # ── Step 2: picker (no Swiggy token → mock MCP restaurant data) ─────
    page.wait_for_selector("#pickerState", state="visible", timeout=15_000)
    expect(page.locator("#dcard-0")).to_be_visible()
    expect(page.locator("#fcard-0")).to_be_visible()
    page.click("#dcard-0")
    page.click("#fcard-0")
    page.click("#generateFromPicker")

    # ── Step 3: plan (Claude mocked — see conftest._mock_claude) ────────
    page.wait_for_selector("#planContent", state="visible", timeout=20_000)
    expect(page.locator("#planContent")).to_contain_text("Farzi Cafe")
    expect(page.locator("#planContent")).to_contain_text("2,350")

    # ── Step 4: refine chat ──────────────────────────────────────────────
    page.fill("#chatInp", "Is Farzi Cafe a good pick for this?")
    page.click("#chatSend")
    expect(page.locator(".chat-bub.ai").last).to_contain_text(
        "romantic pick", timeout=15_000
    )


def test_reload_keeps_the_session(authed_page):
    """A logged-in session survives a page reload (localStorage persists)."""
    page, static_base = authed_page
    page.goto(f"{static_base}/demo.html")
    page.wait_for_selector("#loadScreen", state="hidden", timeout=10_000)
    expect(page.locator("#acctLabel")).not_to_have_text("Sign in")

    page.reload()
    page.wait_for_selector("#loadScreen", state="hidden", timeout=10_000)
    expect(page.locator("#acctLabel")).not_to_have_text("Sign in")
