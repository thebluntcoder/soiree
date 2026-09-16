"""
tests/unit/test_orders_endpoint.py — POST /plans/{plan_id}/order.

Calls the endpoint function directly (plan_service functions + get_access_token
monkeypatched on the plans module), matching this repo's convention (see
test_search.py / test_auth.py). approve_and_claim_for_ordering's actual
atomicity is a DB-level property (a conditional UPDATE ... WHERE status=
'ready') that a monkeypatched stub can't prove under concurrency — that's
exercised by the E2E order-placement test against a real Postgres instead.
This file only proves place_order's own branching: which HTTP status each
precondition failure gets, and that the happy path claims the plan and
enqueues the booking task with the right arguments.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks, HTTPException

from app.api.v1.endpoints import plans


def _user():
    return SimpleNamespace(id="u1")


def _plan(**overrides):
    defaults = dict(id="p1", user_id="u1", status="ready", dineout_selection='{"restaurant_id":"r1"}')
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


async def _call(plan_id="p1", services=None, monkeypatch=None, **patches):
    request = plans.OrderRequest(services=services or ["dineout"])
    bg = BackgroundTasks()
    for name, value in patches.items():
        monkeypatch.setattr(plans, name, value)
    return await plans.place_order(
        plan_id, request, bg, user=_user(), session=object()
    ), bg


class TestPlaceOrder:
    @pytest.mark.asyncio
    async def test_plan_not_found_is_404(self, monkeypatch):
        with pytest.raises(HTTPException) as exc:
            await _call(monkeypatch=monkeypatch, get_plan=AsyncMock(return_value=None))
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_other_users_plan_is_404(self, monkeypatch):
        plan = _plan(user_id="someone-else")
        with pytest.raises(HTTPException) as exc:
            await _call(monkeypatch=monkeypatch, get_plan=AsyncMock(return_value=plan))
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_unsupported_service_is_422(self, monkeypatch):
        with pytest.raises(HTTPException) as exc:
            await _call(
                services=["food"], monkeypatch=monkeypatch,
                get_plan=AsyncMock(return_value=_plan()),
            )
        assert exc.value.status_code == 422
        assert "food" in exc.value.detail

    @pytest.mark.asyncio
    async def test_no_dineout_selection_is_422(self, monkeypatch):
        plan = _plan(dineout_selection=None)
        with pytest.raises(HTTPException) as exc:
            await _call(monkeypatch=monkeypatch, get_plan=AsyncMock(return_value=plan))
        assert exc.value.status_code == 422
        assert "no Dineout selection" in exc.value.detail

    @pytest.mark.asyncio
    async def test_not_ready_is_409(self, monkeypatch):
        with pytest.raises(HTTPException) as exc:
            await _call(
                monkeypatch=monkeypatch,
                get_plan=AsyncMock(return_value=_plan()),
                approve_and_claim_for_ordering=AsyncMock(return_value=(None, "ordering")),
            )
        assert exc.value.status_code == 409
        assert "ordering" in exc.value.detail

    @pytest.mark.asyncio
    async def test_happy_path_claims_and_enqueues(self, monkeypatch):
        claimed_plan = _plan(status="ordering")
        result, bg = await _call(
            monkeypatch=monkeypatch,
            get_plan=AsyncMock(return_value=_plan()),
            approve_and_claim_for_ordering=AsyncMock(return_value=(claimed_plan, None)),
            get_access_token=AsyncMock(return_value="swiggy-tok"),
        )

        assert result == {"plan_id": "p1", "status": "ordering"}
        assert len(bg.tasks) == 1
        task = bg.tasks[0]
        assert task.func is plans.place_dineout_booking
        assert task.args == ("p1", "swiggy-tok")
