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
from app.services.mcp.orchestrator import MCPOrchestrator


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
        """Hybrid mode: 50% Dineout, 35% Food, 15% Instamart."""
        orchestrator = MCPOrchestrator()
        split = orchestrator._calculate_budget_split(3000, "hybrid")
        assert split["dineout"] == int(3000 * 0.50)
        assert split["food"] == int(3000 * 0.35)
        assert split["instamart"] == int(3000 * 0.15)

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