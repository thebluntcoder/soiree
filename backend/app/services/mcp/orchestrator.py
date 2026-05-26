"""
services/mcp/orchestrator.py — Parallel MCP call coordinator.

CONCEPT: Why this file exists
--------------------------------
The AI planner needs data from up to 3 Swiggy MCP servers before it can
generate a plan. Without an orchestrator, you'd write this logic scattered
across the planner, or call them one by one (slow).

The orchestrator's single job: given event context, resolve addresses,
fire all relevant MCP calls IN PARALLEL, and return a unified context dict.

CONCEPT: asyncio.gather() — the core performance win
------------------------------------------------------
asyncio.gather() takes multiple coroutines and runs them concurrently
on the same event loop thread. Crucially, it doesn't use threads or
processes — it's cooperative multitasking.

Timeline comparison for a hybrid event:
  Serial:   Food(300ms) → Instamart(200ms) → Dineout(250ms) = 750ms total
  Parallel: all three fire at once → done in ~300ms (longest one wins)

That's 2.5x faster with zero extra complexity.

CONCEPT: return_exceptions=True
---------------------------------
If one MCP server is down or rate-limited, we don't want the whole plan
to fail. `return_exceptions=True` means gather() returns the Exception
object instead of raising it. We check for this in _process_results()
and degrade gracefully — the plan still generates with data from the
working services, with a note about what's unavailable.

CONCEPT: Address scopes differ per server
------------------------------------------
This is the most important thing to understand about the Swiggy MCP platform:

  Food/Instamart → use addressId from get_addresses()
  Dineout        → use lat/lng from get_saved_locations()

These are DIFFERENT scopes — never pass an addressId to Dineout search,
or lat/lng to Food search. The orchestrator resolves both before the
parallel gather, then passes the correct identifier to each client.

CONCEPT: Venue mode drives which MCPs are called
--------------------------------------------------
  out    → Dineout only
  home   → Food + Instamart
  hybrid → all three (Dineout + Food + Instamart)

Calling unused MCPs wastes time and rate limit quota. The orchestrator
only fires what's needed for the chosen venue mode.
"""

import asyncio
from typing import Any
from app.services.mcp.food import FoodMCPClient
from app.services.mcp.instamart import InstamartMCPClient
from app.services.mcp.dineout import DineoutMCPClient


# Default mock values — used when running in mock mode (no real credentials)
DEFAULT_MOCK_ADDRESS_ID = "addr_001"
DEFAULT_MOCK_LOCATION = {"lat": 26.8467, "lng": 80.9462}  # Lucknow coordinates


class MCPOrchestrator:
    """
    Coordinates parallel calls to all three Swiggy MCP servers.

    Instantiated once and reused across requests (stateless).
    Each method call is independent — safe for concurrent use.

    Address resolution happens before the parallel gather:
      - Food + Instamart: addressId from get_addresses()
      - Dineout: lat/lng from get_saved_locations()

    Usage:
        orchestrator = MCPOrchestrator()
        context = await orchestrator.gather_context(
            location="Lucknow",
            event_type="date",
            venue_mode="hybrid",
            dietary_tags=["Veg"],
            guest_count=2,
            budget=3000,
            start_hour=20,
        )
        # context["food"], context["instamart"], context["dineout"]
        # are all populated — pass directly to AI planner
    """

    def __init__(self):
        # Each client is lightweight — just holds config, no open connections
        self.food = FoodMCPClient()
        self.instamart = InstamartMCPClient()
        self.dineout = DineoutMCPClient()

    async def resolve_addresses(self) -> tuple[str, dict]:
        """
        Resolve user's address and location in parallel before the main gather.

        Returns:
            (addressId, {lat, lng}) tuple:
              - addressId: passed to Food + Instamart search_restaurants/search_products
              - {lat, lng}: passed to Dineout search_restaurants_dineout

        In mock mode returns hardcoded values immediately.
        In live mode calls get_addresses() and get_saved_locations() in parallel.
        """
        if self.food.use_mock:
            # Mock mode — return hardcoded defaults, no network calls needed
            return DEFAULT_MOCK_ADDRESS_ID, DEFAULT_MOCK_LOCATION

        # Live mode — resolve both in parallel
        addresses_result, locations_result = await asyncio.gather(
            self.food.get_addresses(),
            self.dineout.get_saved_locations(),
            return_exceptions=True,
        )

        # Extract addressId for Food/Instamart
        address_id = DEFAULT_MOCK_ADDRESS_ID
        if not isinstance(addresses_result, Exception):
            addresses = addresses_result.get("data", [])
            if addresses:
                # Prefer "Home" label, fall back to first address
                home = next(
                    (a for a in addresses if a.get("label") == "Home"), addresses[0]
                )
                address_id = home.get("id", DEFAULT_MOCK_ADDRESS_ID)

        # Extract lat/lng for Dineout
        location = DEFAULT_MOCK_LOCATION
        if not isinstance(locations_result, Exception):
            locations = locations_result.get("data", [])
            if locations:
                loc = locations[0]
                location = {
                    "lat": loc.get("lat", 26.8467),
                    "lng": loc.get("lng", 80.9462),
                }

        return address_id, location

    async def gather_context(
        self,
        location: str,
        event_type: str,
        venue_mode: str,
        dietary_tags: list[str],
        guest_count: int,
        budget: int,
        start_hour: float,
        health_focus: int = 50,
        lat: float | None = None,
        lng: float | None = None,
    ) -> dict[str, Any]:
        """
        Resolve addresses then fire all relevant MCP calls in parallel.

        The budget is split across services based on venue_mode:
          - out:    100% to Dineout
          - home:   70% Food, 30% Instamart
          - hybrid: 50% Dineout, 35% Food, 15% Instamart

        Args:
            location: city/area text string (used for display in prompts)
            event_type: date/friends/birthday/corporate/house_party/family
            venue_mode: out/home/hybrid — controls which MCPs are called
            dietary_tags: group-level dietary restrictions
            guest_count: total number of people including host
            budget: total INR budget across all Swiggy services
            start_hour: event start time in 24h (float for 30-min slots e.g. 20.5)
            health_focus: 0-100 wellness slider
            lat: optional device GPS latitude — improves Dineout accuracy
            lng: optional device GPS longitude — improves Dineout accuracy

        Returns:
            dict with keys for each called service:
              {
                "food": {...} or {"error": "...", "data": []},
                "instamart": {...} or None,   # None if not applicable
                "dineout": {...} or None,     # None if not applicable
                "venue_mode": "hybrid",
                "budget_split": {"food": 1050, "instamart": 450, "dineout": 1500},
                "resolved_location": "Lucknow",
                "coordinates": {"lat": 26.84, "lng": 80.94},
              }
        """
        budget_split = self._calculate_budget_split(budget, venue_mode)

        # Step 1: Resolve addresses before parallel MCP calls
        address_id, saved_location = await self.resolve_addresses()

        # Use device GPS if provided (more accurate than saved location)
        # otherwise fall back to the user's saved location from Swiggy
        dineout_lat = lat or saved_location["lat"]
        dineout_lng = lng or saved_location["lng"]

        # Step 2: Build the list of coroutines based on venue_mode
        # Each entry: (service_name, coroutine)
        tasks: list[tuple[str, Any]] = []

        # Food delivery — called for home and hybrid modes
        if venue_mode in ("home", "hybrid"):
            tasks.append(
                (
                    "food",
                    self.food.search_restaurants(
                        address_id=address_id,
                        query=_cuisine_query(event_type, dietary_tags),
                        dietary_filters=dietary_tags,
                        budget_per_head=budget_split["food"] // max(guest_count, 1),
                        health_focus=health_focus,
                    ),
                )
            )

        # Instamart groceries — called for home and hybrid modes
        if venue_mode in ("home", "hybrid"):
            tasks.append(
                (
                    "instamart",
                    self.instamart.search_products(
                        address_id=address_id,
                        query=_instamart_query(event_type),
                        event_type=event_type,
                        guest_count=guest_count,
                        dietary_tags=dietary_tags,
                        budget=budget_split["instamart"],
                    ),
                )
            )

        # Dineout reservations — called for out and hybrid modes
        if venue_mode in ("out", "hybrid"):
            tasks.append(
                (
                    "dineout",
                    self.dineout.search_restaurants(
                        lat=dineout_lat,
                        lng=dineout_lng,
                        query=_dineout_query(event_type, dietary_tags),
                        guest_count=guest_count,
                        dietary_filters=dietary_tags,
                        event_type=event_type,
                        budget_per_head=budget_split["dineout"] // max(guest_count, 1),
                        start_hour=int(start_hour),
                    ),
                )
            )

        # Step 3: Fire all tasks concurrently
        # return_exceptions=True: failed tasks return Exception instead of raising
        service_names = [name for name, _ in tasks]
        coroutines = [coro for _, coro in tasks]
        results = await asyncio.gather(*coroutines, return_exceptions=True)

        # Step 4: Process results — handle errors gracefully per service
        context = self._process_results(service_names, results)
        context["venue_mode"] = venue_mode
        context["budget_split"] = budget_split
        context["resolved_location"] = location
        context["coordinates"] = {"lat": dineout_lat, "lng": dineout_lng}

        return context

    def _calculate_budget_split(self, total: int, venue_mode: str) -> dict[str, int]:
        """
        Split total budget across Swiggy services.

        Splits are based on typical event spending patterns:
          out:    entire budget to Dineout (no delivery or groceries)
          home:   most to food delivery, some to Instamart supplies
          hybrid: half to restaurant, rest split between delivery + groceries

        Returns dict with INR amounts per service.
        """
        if venue_mode == "out":
            return {"dineout": total, "food": 0, "instamart": 0}
        elif venue_mode == "home":
            return {
                "dineout": 0,
                "food": int(total * 0.70),
                "instamart": int(total * 0.30),
            }
        else:  # hybrid
            return {
                "dineout": int(total * 0.50),
                "food": int(total * 0.35),
                "instamart": int(total * 0.15),
            }

    def _process_results(
        self,
        service_names: list[str],
        results: list[Any],
    ) -> dict[str, Any]:
        """
        Map gather() results back to service names, handle exceptions gracefully.

        If a service failed, we store {"error": str(e), "data": []} so the
        AI planner knows it's unavailable and can note it in the plan
        rather than crashing the entire generation.
        """
        context: dict[str, Any] = {
            "food": None,
            "instamart": None,
            "dineout": None,
        }

        for service_name, result in zip(service_names, results):
            if isinstance(result, Exception):
                # Log the error but don't crash — degrade gracefully
                context[service_name] = {
                    "error": str(result),
                    "data": [],
                    "note": f"{service_name} data unavailable — showing partial plan",
                }
            else:
                context[service_name] = result

        return context


# ── Search query helpers ─────────────────────────────────────────────────────


def _cuisine_query(event_type: str, dietary_tags: list[str]) -> str:
    """
    Map event type and dietary tags to a Food search query.
    Dietary tags take priority — "Veg" overrides event-type cuisine.
    """
    if "Veg" in dietary_tags or "Jain" in dietary_tags:
        return "vegetarian"
    queries = {
        "date": "fine dining romantic",
        "birthday": "celebration cake dessert",
        "corporate": "healthy business lunch",
        "house_party": "snacks finger food",
        "family": "family meals",
        "friends": "popular casual",
    }
    return queries.get(event_type, "popular")


def _instamart_query(event_type: str) -> str:
    """Map event type to an Instamart product search query."""
    queries = {
        "date": "candles chocolates juice",
        "birthday": "cake candles balloons decoration",
        "corporate": "coffee tea biscuits water",
        "house_party": "chips drinks snacks",
        "family": "snacks beverages",
        "friends": "chips drinks snacks",
    }
    return queries.get(event_type, "snacks beverages")


def _dineout_query(event_type: str, dietary_tags: list[str]) -> str:
    """Map event type and dietary tags to a Dineout search query."""
    if "Veg" in dietary_tags:
        return "vegetarian restaurant"
    queries = {
        "date": "romantic rooftop fine dining",
        "birthday": "celebration party hall",
        "corporate": "business dining professional",
        "house_party": "",
        "family": "family restaurant spacious",
        "friends": "casual dining popular",
    }
    return queries.get(event_type, "")
