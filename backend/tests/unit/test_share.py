"""
tests/unit/test_share.py — the read-only public plan link.

POST/DELETE /plans/{id}/share (owner-only) and GET /shared/{token} (public).

Endpoint functions are called directly with monkeypatched service calls,
matching this repo's convention (see test_orders_endpoint.py). Like
approve_and_claim_for_ordering, enable_sharing's actual race-safety is a
DB-level property (a conditional UPDATE ... WHERE share_token IS NULL)
that a stub can't prove — the E2E share test exercises it against a real
Postgres. What matters here is the *access model*, which is the part that
would be a security bug if it regressed:

  - only the owner can turn sharing on/off (404 for anyone else, not 403 —
    don't confirm a plan ID exists)
  - the public endpoint returns an explicit allowlist, never the Plan row
  - the public endpoint stays unauthenticated and the owner endpoints stay
    authenticated (checked structurally against the real route table)
"""

import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Response
from fastapi.routing import APIRoute

from app.api.v1.deps import current_user
from app.api.v1.endpoints import plans, shared
from app.schemas.plan import SharedPlanView


def _user(uid="u1"):
    return SimpleNamespace(id=uid)


def _plan(**overrides):
    defaults = dict(id="p1", user_id="u1", status="ready")
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestSharePlan:
    @pytest.mark.asyncio
    async def test_not_found_is_404(self, monkeypatch):
        monkeypatch.setattr(plans, "get_plan", AsyncMock(return_value=None))
        with pytest.raises(HTTPException) as exc:
            await plans.share_plan("p1", user=_user(), session=object())
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_someone_elses_plan_is_404_not_403(self, monkeypatch):
        monkeypatch.setattr(
            plans, "get_plan", AsyncMock(return_value=_plan(user_id="someone-else"))
        )
        enable = AsyncMock()
        monkeypatch.setattr(plans, "enable_sharing", enable)
        with pytest.raises(HTTPException) as exc:
            await plans.share_plan("p1", user=_user(), session=object())
        assert exc.value.status_code == 404
        enable.assert_not_called()

    @pytest.mark.asyncio
    async def test_plan_still_generating_is_409(self, monkeypatch):
        monkeypatch.setattr(
            plans, "get_plan", AsyncMock(return_value=_plan(status="generating"))
        )
        enable = AsyncMock()
        monkeypatch.setattr(plans, "enable_sharing", enable)
        with pytest.raises(HTTPException) as exc:
            await plans.share_plan("p1", user=_user(), session=object())
        assert exc.value.status_code == 409
        enable.assert_not_called()

    @pytest.mark.asyncio
    async def test_happy_path_returns_the_token(self, monkeypatch):
        monkeypatch.setattr(plans, "get_plan", AsyncMock(return_value=_plan()))
        enable = AsyncMock(return_value="tok_abc")
        monkeypatch.setattr(plans, "enable_sharing", enable)
        result = await plans.share_plan("p1", user=_user(), session=object())
        assert result == {"share_token": "tok_abc"}
        enable.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["ready", "approved", "ordering", "confirmed", "failed"])
    async def test_any_non_generating_status_can_be_shared(self, monkeypatch, status):
        monkeypatch.setattr(plans, "get_plan", AsyncMock(return_value=_plan(status=status)))
        monkeypatch.setattr(plans, "enable_sharing", AsyncMock(return_value="t"))
        result = await plans.share_plan("p1", user=_user(), session=object())
        assert result["share_token"] == "t"


class TestUnsharePlan:
    @pytest.mark.asyncio
    async def test_someone_elses_plan_is_404_and_nothing_is_revoked(self, monkeypatch):
        monkeypatch.setattr(
            plans, "get_plan", AsyncMock(return_value=_plan(user_id="someone-else"))
        )
        disable = AsyncMock()
        monkeypatch.setattr(plans, "disable_sharing", disable)
        with pytest.raises(HTTPException) as exc:
            await plans.unshare_plan("p1", user=_user(), session=object())
        assert exc.value.status_code == 404
        disable.assert_not_called()

    @pytest.mark.asyncio
    async def test_happy_path_revokes(self, monkeypatch):
        monkeypatch.setattr(plans, "get_plan", AsyncMock(return_value=_plan()))
        disable = AsyncMock()
        monkeypatch.setattr(plans, "disable_sharing", disable)
        result = await plans.unshare_plan("p1", user=_user(), session=object())
        assert result == {"share_token": None}
        disable.assert_awaited_once()


# A Plan row with every sensitive field populated with a recognisable value,
# so a leak into the public response is caught by a plain substring search.
def _full_plan():
    return SimpleNamespace(
        id="PLAN-ID-LEAK",
        user_id="USER-ID-LEAK",
        event_id="EVENT-ID-LEAK",
        status="confirmed",
        share_token="the-token",
        created_at=datetime(2026, 9, 20, 18, 30),
        timeline='[{"time": "8:00 PM", "emoji": "🍽", "title": "Dinner", "detail": "x"}]',
        dineout_options="Farzi Cafe — rooftop",
        food_options="Meghana Foods",
        instamart_cart="Tealight candles",
        health_insight="Balanced.",
        active_offers="15% off",
        dineout_selection='{"restaurant_id": "RESTAURANT-ID-LEAK", "available_slots": []}',
        dineout_booking_id="BOOKING-ID-LEAK",
        food_order_id="FOOD-ORDER-LEAK",
        instamart_order_id="INSTAMART-ORDER-LEAK",
        order_error="ORDER-ERROR-LEAK",
        dineout_cost=1800,
        food_cost=400,
        instamart_cost=150,
        total_cost=2350,
        total_savings=325,
        edit_count=3,
    )


def _session_returning(event):
    result = SimpleNamespace(scalar_one_or_none=lambda: event)
    return SimpleNamespace(execute=AsyncMock(return_value=result))


def _event():
    return SimpleNamespace(
        event_type="date", guest_count=2, location="221B HOME-ADDRESS-LEAK Street"
    )


class TestViewSharedPlan:
    @pytest.mark.asyncio
    async def test_unknown_or_revoked_token_is_a_generic_404(self, monkeypatch):
        monkeypatch.setattr(shared, "get_plan_by_share_token", AsyncMock(return_value=None))
        with pytest.raises(HTTPException) as exc:
            await shared.view_shared_plan("nope", Response(), session=object())
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_response_is_the_allowlist_and_nothing_else(self, monkeypatch):
        monkeypatch.setattr(
            shared, "get_plan_by_share_token", AsyncMock(return_value=_full_plan())
        )
        view = await shared.view_shared_plan(
            "the-token", Response(), session=_session_returning(_event())
        )
        # Hard-coded on purpose: adding a field to SharedPlanView (i.e.
        # publishing something new to anyone with a link) must fail here
        # until it's added below by someone who meant to.
        assert set(view.model_dump()) == {
            "event_type", "guest_count", "created_at", "timeline",
            "dineout_options", "food_options", "instamart_cart",
            "health_insight", "active_offers", "dineout_cost", "food_cost",
            "instamart_cost", "total_cost", "total_savings",
        }

    @pytest.mark.asyncio
    async def test_no_identifier_or_private_field_leaks(self, monkeypatch):
        monkeypatch.setattr(
            shared, "get_plan_by_share_token", AsyncMock(return_value=_full_plan())
        )
        view = await shared.view_shared_plan(
            "the-token", Response(), session=_session_returning(_event())
        )
        blob = json.dumps(view.model_dump(), default=str)
        for secret in (
            "PLAN-ID-LEAK", "USER-ID-LEAK", "EVENT-ID-LEAK", "RESTAURANT-ID-LEAK",
            "BOOKING-ID-LEAK", "FOOD-ORDER-LEAK", "INSTAMART-ORDER-LEAK",
            "ORDER-ERROR-LEAK", "HOME-ADDRESS-LEAK", "the-token",
        ):
            assert secret not in blob, f"{secret} leaked into the public response"

    @pytest.mark.asyncio
    async def test_plan_content_round_trips(self, monkeypatch):
        monkeypatch.setattr(
            shared, "get_plan_by_share_token", AsyncMock(return_value=_full_plan())
        )
        view = await shared.view_shared_plan(
            "the-token", Response(), session=_session_returning(_event())
        )
        assert view.event_type == "date"
        assert view.guest_count == 2
        assert view.total_cost == 2350
        assert view.dineout_options == "Farzi Cafe — rooftop"
        assert view.created_at == "2026-09-20T18:30:00"

    @pytest.mark.asyncio
    async def test_missing_parent_event_still_renders(self, monkeypatch):
        monkeypatch.setattr(
            shared, "get_plan_by_share_token", AsyncMock(return_value=_full_plan())
        )
        view = await shared.view_shared_plan(
            "the-token", Response(), session=_session_returning(None)
        )
        assert view.event_type is None
        assert view.total_cost == 2350

    @pytest.mark.asyncio
    async def test_response_is_marked_no_store(self, monkeypatch):
        """So a revoked link can't keep being served from a browser/edge cache."""
        monkeypatch.setattr(
            shared, "get_plan_by_share_token", AsyncMock(return_value=_full_plan())
        )
        response = Response()
        await shared.view_shared_plan(
            "the-token", response, session=_session_returning(_event())
        )
        assert response.headers["cache-control"] == "no-store"


def _dependency_calls(route: APIRoute) -> set:
    """Every callable in a route's dependency tree (recursively)."""
    found, stack = set(), list(route.dependant.dependencies)
    while stack:
        dep = stack.pop()
        found.add(dep.call)
        stack.extend(dep.dependencies)
    return found


class TestAccessModelIsWiredCorrectly:
    """Checked against the real route table, not just the function bodies —
    the whole point of the feature is *who can call what*."""

    def _routes(self):
        from app.main import app

        return {
            (r.path, m): r
            for r in app.routes
            if isinstance(r, APIRoute)
            for m in r.methods
        }

    def test_public_endpoint_requires_no_login(self):
        route = self._routes()[("/api/v1/shared/{token}", "GET")]
        assert current_user not in _dependency_calls(route)

    @pytest.mark.parametrize("method", ["POST", "DELETE"])
    def test_owner_endpoints_require_login(self, method):
        route = self._routes()[("/api/v1/plans/{plan_id}/share", method)]
        assert current_user in _dependency_calls(route)
