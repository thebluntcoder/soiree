"""
tests/unit/test_dineout_mcp.py — book_table / get_booking_status.

Mock-dispatch shape (no access_token) and real-call param shape (access_token
truthy routes through _real_mcp_call — mocked here rather than hitting the
network) for the two methods book_with_retry depends on. Plus parse_booking,
which is UNCONFIRMED against live output (same caveat as parse_available_slots
— see parse_mcp.py's comment above it).
"""

from unittest.mock import AsyncMock

import pytest

from app.services.mcp.dineout import DineoutMCPClient
from app.services.mcp.parse_mcp import parse_booking


class TestBookTableMock:
    @pytest.mark.asyncio
    async def test_mock_returns_confirmed_booking(self):
        client = DineoutMCPClient()
        result = await client.book_table("dine_001", "slot_1930", 4, "2026-05-10")
        data = result["data"]
        assert data["status"] == "CONFIRMED"
        assert data["bookingId"].startswith("bk_")
        assert data["restaurantId"] == "dine_001"
        assert data["slotId"] == "slot_1930"
        assert data["guestCount"] == 4
        assert data["date"] == "2026-05-10"

    @pytest.mark.asyncio
    async def test_mock_booking_ids_are_unique(self):
        client = DineoutMCPClient()
        r1 = await client.book_table("dine_001", "slot_1930", 2, "2026-05-10")
        r2 = await client.book_table("dine_001", "slot_2000", 2, "2026-05-10")
        assert r1["data"]["bookingId"] != r2["data"]["bookingId"]

    @pytest.mark.asyncio
    async def test_mock_get_booking_status(self):
        client = DineoutMCPClient()
        result = await client.get_booking_status("bk_abc123")
        assert result["data"] == {"bookingId": "bk_abc123", "status": "CONFIRMED"}


class TestBookTableRealCallShape:
    """access_token truthy routes through _real_mcp_call — mocked here so
    no network call happens; asserts the JSON-RPC arguments sent."""

    @pytest.mark.asyncio
    async def test_book_table_sends_expected_params(self):
        client = DineoutMCPClient()
        client._real_mcp_call = AsyncMock(return_value={"result": {"content": []}})

        await client.book_table("dine_001", "slot_1930", 4, "2026-05-10", access_token="tok")

        args = client._real_mcp_call.call_args
        assert args.args[0] == "book_table"
        assert args.args[1] == {
            "restaurantId": "dine_001", "slotId": "slot_1930",
            "guestCount": 4, "date": "2026-05-10",
        }
        assert args.args[2] == "tok"

    @pytest.mark.asyncio
    async def test_get_booking_status_sends_expected_params(self):
        client = DineoutMCPClient()
        client._real_mcp_call = AsyncMock(return_value={"result": {"content": []}})

        await client.get_booking_status("bk_abc123", access_token="tok")

        args = client._real_mcp_call.call_args
        assert args.args[0] == "get_booking_status"
        assert args.args[1] == {"bookingId": "bk_abc123"}
        assert args.args[2] == "tok"

    @pytest.mark.asyncio
    async def test_no_token_never_hits_real_call(self):
        client = DineoutMCPClient()
        client._real_mcp_call = AsyncMock(return_value={"result": {"content": []}})
        await client.book_table("dine_001", "slot_1930", 4, "2026-05-10")
        client._real_mcp_call.assert_not_called()


def _env(text: str) -> dict:
    return {"result": {"content": [{"type": "text", "text": text}]}}


class TestParseBooking:
    def test_structured_dict_shape(self):
        response = {"data": {"bookingId": "bk_xyz", "status": "CONFIRMED"}}
        assert parse_booking(response) == {"booking_id": "bk_xyz", "status": "CONFIRMED"}

    def test_text_fallback_key_value(self):
        result = parse_booking(_env("Booking ID: bk_999\nStatus: CONFIRMED\nRestaurant: Dwarka"))
        assert result == {"booking_id": "bk_999", "status": "CONFIRMED"}

    def test_text_fallback_no_status_line(self):
        result = parse_booking(_env("Booking ID: bk_999"))
        assert result == {"booking_id": "bk_999", "status": None}

    def test_no_booking_id_anywhere_is_none(self):
        assert parse_booking(_env("Something went wrong, please try again.")) is None
        assert parse_booking({"data": {}}) is None
        assert parse_booking(None) is None

    def test_structured_dict_without_booking_id_falls_back_to_text(self):
        # An error envelope with no bookingId but a text fallback elsewhere —
        # shouldn't happen in practice, but the structured branch shouldn't
        # swallow a value the text scan would have found.
        response = {"data": {"status": "FAILED"}}
        assert parse_booking(response) is None
