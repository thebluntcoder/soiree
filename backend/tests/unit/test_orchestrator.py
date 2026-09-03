"""
tests/unit/test_orchestrator.py — Unit tests for the MCP orchestrator.

WHAT WE TEST:
  - Budget split calculations for all three venue modes
  - Correct MCP clients called per venue mode (out/home/hybrid)
  - Graceful degradation when one service fails
  - asyncio.gather result processing
  - Context dict has correct keys for each venue mode

CONCEPT: Testing async code with pytest-asyncio
-------------------------------------------------
Async functions return coroutines, not values.
pytest-asyncio lets us write async test functions with @pytest.mark.asyncio.
Under the hood it runs them inside an event loop.

CONCEPT: Mocking async methods
--------------------------------
unittest.mock.AsyncMock creates a mock that returns a coroutine.
Regular Mock would return a plain value — awaiting it would crash.
AsyncMock is required for any method called with `await`.
"""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from app.services.mcp.orchestrator import (
    DEFAULT_MOCK_ADDRESS_ID,
    MCPOrchestrator,
    _dineout_query,
    _food_query,
    _parse_address_id,
)


class TestBudgetSplit:
    """Tests for _calculate_budget_split() — pure function, no async needed."""

    def test_out_mode_full_budget_to_dineout(self):
        """Out mode: 100% goes to Dineout, nothing to Food or Instamart."""
        orchestrator = MCPOrchestrator()
        split = orchestrator._calculate_budget_split(3000, "out")
        assert split["dineout"] == 3000
        assert split["food"] == 0
        assert split["instamart"] == 0

    def test_home_mode_split(self):
        """Home mode: 70% Food, 30% Instamart, 0% Dineout."""
        orchestrator = MCPOrchestrator()
        split = orchestrator._calculate_budget_split(3000, "home")
        assert split["dineout"] == 0
        assert split["food"] == int(3000 * 0.70)
        assert split["instamart"] == int(3000 * 0.30)

    def test_hybrid_mode_split(self):
        """
        Hybrid mode: 60% Dineout, 20% Food, 20% Instamart.

        Food is intentionally small in hybrid — enough for a cake/dessert
        from a bakery, not a second full meal (the guests are dining out).
        """
        orchestrator = MCPOrchestrator()
        split = orchestrator._calculate_budget_split(3000, "hybrid")
        assert split["dineout"] == int(3000 * 0.60)
        assert split["food"] == int(3000 * 0.20)
        assert split["instamart"] == int(3000 * 0.20)

    def test_split_values_are_integers(self):
        """All split values must be integers — no floats stored in DB."""
        orchestrator = MCPOrchestrator()
        for mode in ["out", "home", "hybrid"]:
            split = orchestrator._calculate_budget_split(3000, mode)
            for key, value in split.items():
                assert isinstance(value, int), f"{key} is not int in {mode} mode"


class TestProcessResults:
    """Tests for _process_results() — maps gather() output to named services."""

    def test_successful_results_mapped_correctly(self):
        """Successful results should be stored under correct service keys."""
        orchestrator = MCPOrchestrator()
        food_data = {"restaurants": [{"name": "Test"}]}
        result = orchestrator._process_results(["food"], [food_data])
        assert result["food"] == food_data
        assert result["instamart"] is None
        assert result["dineout"] is None

    def test_exception_results_stored_as_error_dict(self):
        """
        Failed services should store error dict, not raise exception.
        This is the graceful degradation pattern.
        """
        orchestrator = MCPOrchestrator()
        error = Exception("MCP timeout")
        result = orchestrator._process_results(["food"], [error])
        assert "error" in result["food"]
        assert "MCP timeout" in result["food"]["error"]

    def test_mixed_success_and_failure(self):
        """One failed service should not affect other services."""
        orchestrator = MCPOrchestrator()
        food_data = {"restaurants": []}
        dineout_error = Exception("Dineout unavailable")
        result = orchestrator._process_results(
            ["food", "dineout"],
            [food_data, dineout_error]
        )
        assert result["food"] == food_data
        assert "error" in result["dineout"]


class TestGatherContext:
    """
    Integration-style tests for gather_context().
    We mock the MCP clients to avoid real network calls.
    """

    @pytest.mark.asyncio
    async def test_out_mode_only_calls_dineout(self):
        """
        For out mode, only Dineout MCP should be called.
        Food and Instamart should not be called — they're irrelevant.
        """
        orchestrator = MCPOrchestrator()

        # Replace real MCP methods with mocks
        orchestrator.food.search_restaurants = AsyncMock(return_value={"restaurants": []})
        orchestrator.instamart.search_products = AsyncMock(return_value={"categories": []})
        orchestrator.dineout.search_restaurants = AsyncMock(return_value={"restaurants": []})

        await orchestrator.gather_context(
            location="Lucknow",
            event_type="date",
            venue_mode="out",
            dietary_tags=[],
            guest_count=2,
            budget=3000,
            start_hour=20,
        )

        # Only Dineout should have been called
        orchestrator.dineout.search_restaurants.assert_called_once()
        orchestrator.food.search_restaurants.assert_not_called()
        orchestrator.instamart.search_products.assert_not_called()

    @pytest.mark.asyncio
    async def test_home_mode_only_calls_food_and_instamart(self):
        """For home mode, only Food and Instamart should be called."""
        orchestrator = MCPOrchestrator()
        orchestrator.food.search_restaurants = AsyncMock(return_value={"restaurants": []})
        orchestrator.instamart.search_products = AsyncMock(return_value={"categories": []})
        orchestrator.dineout.search_restaurants = AsyncMock(return_value={"restaurants": []})

        await orchestrator.gather_context(
            location="Lucknow",
            event_type="date",
            venue_mode="home",
            dietary_tags=[],
            guest_count=2,
            budget=3000,
            start_hour=20,
        )

        orchestrator.food.search_restaurants.assert_called_once()
        orchestrator.instamart.search_products.assert_called_once()
        orchestrator.dineout.search_restaurants.assert_not_called()

    @pytest.mark.asyncio
    async def test_hybrid_mode_calls_all_three(self):
        """Hybrid mode must call all three MCP servers."""
        orchestrator = MCPOrchestrator()
        orchestrator.food.search_restaurants = AsyncMock(return_value={"restaurants": []})
        orchestrator.instamart.search_products = AsyncMock(return_value={"categories": []})
        orchestrator.dineout.search_restaurants = AsyncMock(return_value={"restaurants": []})

        await orchestrator.gather_context(
            location="Lucknow",
            event_type="date",
            venue_mode="hybrid",
            dietary_tags=[],
            guest_count=2,
            budget=3000,
            start_hour=20,
        )

        orchestrator.food.search_restaurants.assert_called_once()
        orchestrator.instamart.search_products.assert_called_once()
        orchestrator.dineout.search_restaurants.assert_called_once()

    @pytest.mark.asyncio
    async def test_context_contains_venue_mode(self):
        """Returned context must include venue_mode for downstream use."""
        orchestrator = MCPOrchestrator()
        orchestrator.food.search_restaurants = AsyncMock(return_value={"restaurants": []})
        orchestrator.instamart.search_products = AsyncMock(return_value={"categories": []})

        context = await orchestrator.gather_context(
            location="Lucknow",
            event_type="friends",
            venue_mode="home",
            dietary_tags=["Veg"],
            guest_count=4,
            budget=2000,
            start_hour=19,
        )

        assert context["venue_mode"] == "home"
        assert "budget_split" in context

    @pytest.mark.asyncio
    async def test_one_service_failure_doesnt_crash(self):
        """
        If one MCP service throws, the others should still return.
        This tests the graceful degradation behaviour.
        """
        orchestrator = MCPOrchestrator()
        orchestrator.food.search_restaurants = AsyncMock(
            side_effect=Exception("Food MCP down")
        )
        orchestrator.instamart.search_products = AsyncMock(
            return_value={"categories": []}
        )

        # Should not raise
        context = await orchestrator.gather_context(
            location="Lucknow",
            event_type="friends",
            venue_mode="home",
            dietary_tags=[],
            guest_count=4,
            budget=2000,
            start_hour=19,
        )

        # Food should have an error, Instamart should have data
        assert "error" in context["food"]
        assert context["instamart"] == {"categories": []}


def _mcp_text(text: str) -> dict:
    """Wrap a string in the real Swiggy MCP response envelope."""
    return {"result": {"content": [{"type": "text", "text": text}]}}


class TestParseAddressId:
    """
    _parse_address_id turns Swiggy's plain-text get_addresses response into
    a single addressId (points #2 coverage — was previously untested).
    """

    SAMPLE = (
        "Found 3 saved addresses:\n"
        "1. [Work] Uttkarsh Mishra: Vibhuti Khand, Gomti Nagar, Lucknow (ID: 99887766)\n"
        "2. [Home] Uttkarsh Mishra: E-1/432, LDA Colony, Lucknow (ID: 43530781)\n"
        "3. [Other] Mom: Sector 12, Noida (ID: 11112222)"
    )

    def test_prefers_city_match(self):
        # "Noida" line is the only one for that city
        assert (
            _parse_address_id(_mcp_text(self.SAMPLE), preferred_city="Noida")
            == "11112222"
        )

    def test_falls_back_to_home_label(self):
        # No city match → first [Home] line wins
        assert (
            _parse_address_id(_mcp_text(self.SAMPLE), preferred_city="Mumbai")
            == "43530781"
        )

    def test_falls_back_to_first_id(self):
        text = "1. [Work] Someone: an address (ID: aaa111)\n2. [Work] Other (ID: bbb222)"
        assert _parse_address_id(_mcp_text(text), preferred_city="Nowhere") == "aaa111"

    def test_empty_text_returns_default(self):
        assert _parse_address_id(_mcp_text(""), "Lucknow") == DEFAULT_MOCK_ADDRESS_ID

    def test_malformed_response_returns_default(self):
        assert _parse_address_id({"nope": True}) == DEFAULT_MOCK_ADDRESS_ID


class TestSearchQueryBuilders:
    """The query builders that turn event context into MCP search strings."""

    def test_food_query_reads_cake_from_notes(self):
        q = _food_query("birthday", [], "please order a chocolate cake", "hybrid")
        assert "cake" in q or "bakery" in q

    def test_food_hybrid_defaults_to_celebration_items(self):
        q = _food_query("birthday", [], None, "hybrid")
        assert "cake" in q or "bakery" in q or "dessert" in q

    def test_dineout_query_picks_cuisine_from_notes(self):
        assert _dineout_query("date", [], "any", "somewhere italian please") == "italian"

    def test_dineout_query_respects_no_alcohol(self):
        assert _dineout_query("family", [], "no", None) == "family"

    def test_dineout_query_veg_restriction(self):
        assert _dineout_query("friends", ["Veg"], "any", None) == "vegetarian"