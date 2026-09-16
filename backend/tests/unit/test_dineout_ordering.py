"""
tests/unit/test_dineout_ordering.py — book_with_retry's retry/idempotency
state machine. AsyncMock on a DineoutMCPClient instance's methods, same
style as TestEnrichDineout in test_planner.py. asyncio.sleep is patched to
a no-op so the real 2s/5s backoff doesn't slow the suite down.
"""

from unittest.mock import AsyncMock

import httpx
import pytest

from app.services.mcp.dineout import DineoutMCPClient
from app.services.orders.dineout_ordering import MAX_ATTEMPTS, book_with_retry


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr("app.services.orders.dineout_ordering.asyncio.sleep", AsyncMock())


def _env(text: str) -> dict:
    return {"result": {"content": [{"type": "text", "text": text}]}}


def _booking_env(booking_id: str, status: str = "CONFIRMED") -> dict:
    return {"data": {"bookingId": booking_id, "status": status}}


def _slots_env(available_slot_ids: list[str], all_slot_ids: list[str] | None = None) -> dict:
    all_slot_ids = all_slot_ids or available_slot_ids
    # parse_available_slots keys on time strings + optional (ID: x) — build
    # text it actually parses: one fabricated time per slot, ID = slotId.
    times = ["7:00 PM", "7:30 PM", "8:00 PM", "8:30 PM"]
    text = "\n".join(
        f"{times[i % len(times)]} - {'Available' if sid in available_slot_ids else 'Booked'} (ID: {sid})"
        for i, sid in enumerate(all_slot_ids)
    )
    return _env(text)


def _http_error(status_code: int, body: dict | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://mcp.swiggy.com/dineout")
    response = httpx.Response(status_code, json=body or {"detail": "error"}, request=request)
    return httpx.HTTPStatusError("error", request=request, response=response)


def _client(book_table_side_effect, **extra_mocks) -> DineoutMCPClient:
    client = DineoutMCPClient()
    client.book_table = AsyncMock(side_effect=book_table_side_effect)
    for name, value in extra_mocks.items():
        setattr(client, name, value)
    return client


COMMON_ARGS = dict(
    restaurant_id="dine_001", slot_id="slot_1930", guest_count=4,
    booking_date="2026-05-10", start_hour=19.5, access_token="tok",
)


class TestBookWithRetry:
    @pytest.mark.asyncio
    async def test_success_on_first_attempt(self):
        client = _client([_booking_env("bk_001")])
        outcome = await book_with_retry(client, **COMMON_ARGS)
        assert outcome.success is True
        assert outcome.booking_id == "bk_001"
        assert client.book_table.call_count == 1

    @pytest.mark.asyncio
    async def test_permission_error_never_retries(self):
        client = _client([PermissionError("SWIGGY_TOKEN_EXPIRED")])
        outcome = await book_with_retry(client, **COMMON_ARGS)
        assert outcome.success is False
        assert outcome.error == "SWIGGY_TOKEN_EXPIRED"
        assert client.book_table.call_count == 1

    @pytest.mark.asyncio
    async def test_5xx_then_slot_still_available_retries_and_succeeds(self):
        client = _client([_http_error(503), _booking_env("bk_002")])
        client.get_available_slots = AsyncMock(return_value=_slots_env(["slot_1930"]))
        client.get_booking_status = AsyncMock()

        outcome = await book_with_retry(client, **COMMON_ARGS)

        assert outcome.success is True
        assert outcome.booking_id == "bk_002"
        assert client.book_table.call_count == 2
        client.get_booking_status.assert_not_called()  # no bookingId in the 503 body

    @pytest.mark.asyncio
    async def test_5xx_then_slot_gone_marks_ambiguous_no_retry(self):
        client = _client([_http_error(503)])
        client.get_available_slots = AsyncMock(return_value=_slots_env([], all_slot_ids=["slot_1930"]))

        outcome = await book_with_retry(client, **COMMON_ARGS)

        assert outcome.success is False
        assert outcome.error == "DINEOUT_BOOKING_AMBIGUOUS"
        assert outcome.ambiguous is True
        assert client.book_table.call_count == 1

    @pytest.mark.asyncio
    async def test_5xx_slots_recheck_itself_fails_is_also_ambiguous(self):
        client = _client([_http_error(503)])
        client.get_available_slots = AsyncMock(side_effect=RuntimeError("mcp down"))

        outcome = await book_with_retry(client, **COMMON_ARGS)

        assert outcome.error == "DINEOUT_BOOKING_AMBIGUOUS"
        assert client.book_table.call_count == 1

    @pytest.mark.asyncio
    async def test_timeout_treated_same_as_5xx(self):
        client = _client([httpx.TimeoutException("timed out"), _booking_env("bk_003")])
        client.get_available_slots = AsyncMock(return_value=_slots_env(["slot_1930"]))

        outcome = await book_with_retry(client, **COMMON_ARGS)

        assert outcome.success is True
        assert client.book_table.call_count == 2

    @pytest.mark.asyncio
    async def test_stale_slot_4xx_refreshes_and_retries_once(self):
        client = _client([_http_error(422), _booking_env("bk_004")])
        # fresh slots: only slot_2000 is bookable now, closest to start_hour=19.5
        client.get_available_slots = AsyncMock(return_value=_slots_env(["slot_2000"]))

        outcome = await book_with_retry(client, **COMMON_ARGS)

        assert outcome.success is True
        assert client.book_table.call_count == 2
        # second attempt used the refreshed slot, not the original stale one
        second_call_slot = client.book_table.call_args_list[1].args[1]
        assert second_call_slot == "slot_2000"

    @pytest.mark.asyncio
    async def test_4xx_twice_fails_slot_unavailable_not_infinite_correction(self):
        client = _client([_http_error(422), _http_error(422)])
        client.get_available_slots = AsyncMock(return_value=_slots_env(["slot_2000"]))

        outcome = await book_with_retry(client, **COMMON_ARGS)

        assert outcome.success is False
        assert outcome.error == "SLOT_UNAVAILABLE"
        # one initial attempt + exactly one corrective retry, no more
        assert client.book_table.call_count == 2

    @pytest.mark.asyncio
    async def test_4xx_no_fresh_slot_available_fails_slot_unavailable(self):
        client = _client([_http_error(422)])
        client.get_available_slots = AsyncMock(return_value=_env("Nothing available today."))

        outcome = await book_with_retry(client, **COMMON_ARGS)

        assert outcome.error == "SLOT_UNAVAILABLE"
        assert client.book_table.call_count == 1

    @pytest.mark.asyncio
    async def test_rpc_value_error_treated_like_4xx(self):
        client = _client([ValueError("MCP error: bad slot"), _booking_env("bk_005")])
        client.get_available_slots = AsyncMock(return_value=_slots_env(["slot_2000"]))

        outcome = await book_with_retry(client, **COMMON_ARGS)

        assert outcome.success is True
        assert client.book_table.call_count == 2

    @pytest.mark.asyncio
    async def test_retries_exhausted_after_max_attempts(self):
        client = _client([_http_error(503)] * MAX_ATTEMPTS)
        client.get_available_slots = AsyncMock(return_value=_slots_env(["slot_1930"]))

        outcome = await book_with_retry(client, **COMMON_ARGS)

        assert outcome.success is False
        assert outcome.error == "RETRY_EXHAUSTED"
        assert client.book_table.call_count == MAX_ATTEMPTS

    @pytest.mark.asyncio
    async def test_booking_id_recovered_from_5xx_body_confirmed_skips_slots_heuristic(self):
        error = _http_error(503, body={"data": {"bookingId": "bk_recovered", "status": "CONFIRMED"}})
        client = _client([error])
        client.get_booking_status = AsyncMock(
            return_value={"data": {"bookingId": "bk_recovered", "status": "CONFIRMED"}}
        )
        client.get_available_slots = AsyncMock()

        outcome = await book_with_retry(client, **COMMON_ARGS)

        assert outcome.success is True
        assert outcome.booking_id == "bk_recovered"
        client.get_available_slots.assert_not_called()

    @pytest.mark.asyncio
    async def test_booking_id_recovered_but_not_found_status_retries(self):
        error = _http_error(503, body={"data": {"bookingId": "bk_ghost", "status": "PENDING_UNKNOWN"}})
        client = _client([error, _booking_env("bk_006")])
        client.get_booking_status = AsyncMock(
            return_value={"data": {"bookingId": "bk_ghost", "status": "NOT_FOUND"}}
        )

        outcome = await book_with_retry(client, **COMMON_ARGS)

        assert outcome.success is True
        assert outcome.booking_id == "bk_006"
        assert client.book_table.call_count == 2
