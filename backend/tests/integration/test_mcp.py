"""
tests/integration/test_mcp.py — Orchestrator ↔ MCP client ↔ prompt, wired
together on the mock path (no network, no access token).

These are "integration" in that they run the real MCPOrchestrator against
the real mock clients and feed the result through the real prompt builder —
the same code path a token-less /plans/generate request takes.
"""

import pytest

from app.services.ai.prompts import build_user_prompt
from app.services.mcp.orchestrator import MCPOrchestrator


async def _context(venue_mode: str) -> dict:
    return await MCPOrchestrator().gather_context(
        location="Lucknow",
        event_type="date",
        venue_mode=venue_mode,
        dietary_tags=[],
        guest_count=2,
        budget=3000,
        start_hour=20,
    )


@pytest.mark.asyncio
async def test_out_mode_has_dineout_only():
    ctx = await _context("out")
    assert ctx["dineout"] is not None
    assert ctx["food"] is None
    assert ctx["instamart"] is None
    assert ctx["budget_split"] == {"dineout": 3000, "food": 0, "instamart": 0}


@pytest.mark.asyncio
async def test_home_mode_has_food_and_instamart():
    ctx = await _context("home")
    assert ctx["dineout"] is None
    assert "restaurants" in ctx["food"]["data"]
    assert "categories" in ctx["instamart"]["data"]


@pytest.mark.asyncio
async def test_hybrid_context_feeds_prompt_with_real_mock_names():
    ctx = await _context("hybrid")
    event = {
        "event_type": "date",
        "venue_mode": "hybrid",
        "location": "Lucknow",
        "start_hour": 20,
        "budget": 3000,
        "guest_count": 2,
        "guests": [],
        "dietary_tags": [],
        "health_focus": 50,
        "notes": None,
        "alcohol_preference": "any",
    }
    prompt = build_user_prompt(event, ctx, offers=[])

    # Names come from the mock clients, not invented by the prompt builder.
    assert "Farzi Cafe" in prompt          # dineout mock
    assert "Tealight Candles" in prompt    # instamart 'date' mock
    assert "Dineout ₹1,800" in prompt      # 60% of 3000


@pytest.mark.asyncio
async def test_one_dead_service_degrades_gracefully(monkeypatch):
    orch = MCPOrchestrator()

    async def boom(*a, **k):
        raise RuntimeError("dineout down")

    monkeypatch.setattr(orch.dineout, "search_restaurants", boom)
    ctx = await orch.gather_context(
        location="Lucknow",
        event_type="friends",
        venue_mode="hybrid",
        dietary_tags=[],
        guest_count=4,
        budget=4000,
        start_hour=20,
    )
    assert "error" in ctx["dineout"]
    assert ctx["food"] is not None
    assert ctx["instamart"] is not None
