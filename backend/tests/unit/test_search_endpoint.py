"""
tests/unit/test_search_endpoint.py — POST /search/ (search_restaurants).

Regression coverage for a real production 500: when MCPOrchestrator.
gather_context() degrades a service to its error shape ({"error": ...,
"data": {}, ...} — see orchestrator.py::_process_results), this endpoint
used to crash with AttributeError: 'list' object has no attribute 'get',
because the error shape's "data" field used to be a list, not a dict, and
_data()'s ctx.get("data", {}) returns whatever's actually there when the
key exists — the {} default only applies when the key is missing. Fixed
at the source (orchestrator.py now uses "data": {}), and at the actual
root cause (base.py now handles SSE-framed MCP responses, which is what
was turning a real, successful dineout search into a false "error" in
the first place). This test locks in the endpoint-level symptom staying
fixed regardless of what causes a future degraded service.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.v1.endpoints import search
from app.schemas.plan import SearchRequest


def _user():
    return SimpleNamespace(id="u1")


def _request(**overrides):
    defaults = dict(
        event_type="date", venue_mode="hybrid", location="Lucknow",
        budget=3000, guest_count=2,
    )
    defaults.update(overrides)
    return SearchRequest(**defaults)


async def _call(monkeypatch, context: dict):
    orch = SimpleNamespace(gather_context=AsyncMock(return_value=context))
    monkeypatch.setattr(search, "get_access_token", AsyncMock(return_value="tok"))
    monkeypatch.setattr(search, "get_orchestrator", lambda: orch)
    return await search.search_restaurants(_request(), user=_user())


class TestSearchRestaurantsDegradedService:
    @pytest.mark.asyncio
    async def test_dineout_degraded_to_error_shape_does_not_500(self, monkeypatch):
        """The exact shape orchestrator._process_results now builds for a
        failed service — this is what used to crash."""
        context = {
            "dineout": {"error": "boom", "data": {}, "note": "dineout data unavailable"},
            "food": None,
            "instamart": None,
            "budget_split": {},
        }
        result = await _call(monkeypatch, context)
        assert result["dineout"] == []
        assert result["dineout_has_more"] is False

    @pytest.mark.asyncio
    async def test_both_services_degraded_does_not_500(self, monkeypatch):
        context = {
            "dineout": {"error": "boom", "data": {}, "note": "..."},
            "food": {"error": "boom", "data": {}, "note": "..."},
            "instamart": None,
            "budget_split": {},
        }
        result = await _call(monkeypatch, context)
        assert result["dineout"] == []
        assert result["food"] == []
        assert result["dineout_has_more"] is False
        assert result["food_has_more"] is False

    @pytest.mark.asyncio
    async def test_successful_dineout_still_populates_normally(self, monkeypatch):
        context = {
            "dineout": {
                "data": {
                    "restaurants": [{"id": "1", "name": "Farzi Cafe", "rating": 4.6}],
                    "hasMore": True,
                }
            },
            "food": None,
            "instamart": None,
            "budget_split": {},
        }
        result = await _call(monkeypatch, context)
        assert result["dineout"][0]["name"] == "Farzi Cafe"
        assert result["dineout_has_more"] is True
