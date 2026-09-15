"""
tests/unit/test_search.py — GET /search/restaurant/{id}'s slot-merging.

Calls the endpoint function directly (get_access_token / get_orchestrator
monkeypatched) rather than through FastAPI's test client, matching this
repo's convention (see test_auth.py). get_restaurant_details itself is
already covered by test_parse_mcp.py / test_planner.py — this file is
about the endpoint's own new behavior: merging available_slots in
alongside details, independently of whether details enrichment worked.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.api.v1.endpoints import search


def _user():
    return SimpleNamespace(id="u1")


def _env(text: str) -> dict:
    return {"result": {"content": [{"type": "text", "text": text}]}}


_DETAILS_ENV = _env(
    "Restaurant: Kwality Restaurant\nCuisines: North Indian\nRating: 4.5"
)
_SLOTS_ENV = _env("7:30 PM - Available\n8:00 PM - Booked")


def _fake_orchestrator(details_env=_DETAILS_ENV, slots_env=_SLOTS_ENV, details_exc=None, slots_exc=None):
    orch = SimpleNamespace(dineout=SimpleNamespace())
    orch.dineout.get_restaurant_details = (
        AsyncMock(side_effect=details_exc) if details_exc else AsyncMock(return_value=details_env)
    )
    orch.dineout.get_available_slots = (
        AsyncMock(side_effect=slots_exc) if slots_exc else AsyncMock(return_value=slots_env)
    )
    return orch


@pytest.mark.asyncio
async def test_merges_slots_alongside_details(monkeypatch):
    orch = _fake_orchestrator()
    monkeypatch.setattr(search, "get_access_token", AsyncMock(return_value="tok"))
    monkeypatch.setattr(search, "get_orchestrator", lambda: orch)

    result = await search.restaurant_details("r1", user=_user())

    assert result["name"] == "Kwality Restaurant"
    assert result["available_slots"] == [{"time": "7:30 PM", "available": True}]


@pytest.mark.asyncio
async def test_no_swiggy_connection_returns_empty(monkeypatch):
    monkeypatch.setattr(search, "get_access_token", AsyncMock(return_value=None))
    result = await search.restaurant_details("r1", user=_user())
    assert result == {}


@pytest.mark.asyncio
async def test_slots_failure_keeps_details(monkeypatch):
    orch = _fake_orchestrator(slots_exc=RuntimeError("500"))
    monkeypatch.setattr(search, "get_access_token", AsyncMock(return_value="tok"))
    monkeypatch.setattr(search, "get_orchestrator", lambda: orch)

    result = await search.restaurant_details("r1", user=_user())

    assert result["name"] == "Kwality Restaurant"
    assert "available_slots" not in result


@pytest.mark.asyncio
async def test_details_failure_still_returns_slots(monkeypatch):
    orch = _fake_orchestrator(details_exc=RuntimeError("500"))
    monkeypatch.setattr(search, "get_access_token", AsyncMock(return_value="tok"))
    monkeypatch.setattr(search, "get_orchestrator", lambda: orch)

    result = await search.restaurant_details("r1", user=_user())

    assert result == {"available_slots": [{"time": "7:30 PM", "available": True}]}


@pytest.mark.asyncio
async def test_guest_count_and_token_passed_to_slots_call(monkeypatch):
    orch = _fake_orchestrator()
    monkeypatch.setattr(search, "get_access_token", AsyncMock(return_value="tok"))
    monkeypatch.setattr(search, "get_orchestrator", lambda: orch)

    await search.restaurant_details("r1", guest_count=6, user=_user())

    kwargs = orch.dineout.get_available_slots.call_args.kwargs
    assert kwargs["guest_count"] == 6
    assert kwargs["access_token"] == "tok"
