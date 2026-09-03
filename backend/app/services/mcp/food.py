"""
services/mcp/food.py — Swiggy Food MCP client.

CONCEPT: What is MCP (Model Context Protocol)?
------------------------------------------------
MCP is a standard protocol that lets AI agents talk to external services
via a defined tool interface. Instead of writing custom API integrations,
you connect to an MCP server and call "tools" by name with structured inputs.

REAL TOOL NAMES (confirmed from Swiggy docs):
  get_addresses       → resolve delivery address (returns addressId)
  search_restaurants  → find restaurants by addressId + query
  get_restaurant_menu → get full menu for a restaurant
  search_menu         → search dishes across restaurants
  update_food_cart    → add/remove items from cart (per-restaurant, flushes on switch)
  flush_food_cart     → explicitly clear cart
  get_food_cart       → get current cart contents + total
  fetch_food_coupons  → available coupons
  apply_food_coupon   → apply a coupon code
  place_food_order    → place the order (Phase 2) — NOT idempotent
  get_food_orders     → order history (use to verify if place_food_order succeeded)
  track_food_order    → track order status (Phase 2)

CRITICAL PRODUCTION NOTES:
  - Food uses addressId (NOT lat/lng) — call get_addresses() first
  - Cart is tied to a single restaurant — switching restaurant flushes it
  - v1 hard cap: ₹1000 per order
  - COD only in v1 — filter coupons where requiresOnlinePayment=False
  - place_food_order is NOT idempotent — on 5xx call get_food_orders before retrying
  - Only recommend restaurants with availabilityStatus: "OPEN"

CONCEPT: Mock-first development
---------------------------------
Without a per-user OAuth token, every method returns mock data whose
shape mirrors real MCP responses exactly. Pass a token and the same
methods hit the real Swiggy MCP server — see base.BaseMCPClient for
the transport. Zero changes to orchestrator.py or planner.py either way.

CONCEPT: Why async methods?
----------------------------
MCP calls are network calls — they take time (50-500ms each).
If we used sync (blocking) Python, the server would freeze during
each call, unable to handle other requests.
With async, we just "await" the result and the event loop handles
other requests in the meantime. This is why asyncio.gather() in
orchestrator.py is so powerful — 3 async calls run concurrently.

MCP URL: https://mcp.swiggy.com/food
"""

import asyncio
from typing import Any

from app.services.mcp.base import BaseMCPClient


class FoodMCPClient(BaseMCPClient):
    """
    Client for Swiggy Food MCP server.

    Wraps all Food MCP tool calls behind clean async methods.
    Returns mock data when a call has no access token, real MCP data
    when it does (transport lives in BaseMCPClient).

    Usage:
        client = FoodMCPClient()
        # Step 1: resolve address (Food requires addressId not lat/lng)
        addresses = await client.get_addresses()
        address_id = addresses["data"][0]["id"]
        # Step 2: search restaurants
        results = await client.search_restaurants(
            address_id=address_id,
            dietary_filters=["Veg"],
            budget_per_head=500,
        )
    """

    MCP_URL = "https://mcp.swiggy.com/food"

    async def _mock_dispatch(self, tool_name: str, params: dict) -> dict:
        """Route mock calls to the appropriate mock method."""
        # Simulate network latency so async behaviour is realistic in dev
        await asyncio.sleep(0.1)

        dispatch = {
            "get_addresses": self._mock_get_addresses,
            "search_restaurants": self._mock_search_restaurants,
            "get_restaurant_menu": self._mock_get_restaurant_menu,
            "search_menu": self._mock_search_menu,
            "fetch_food_coupons": self._mock_fetch_coupons,
            "get_food_cart": self._mock_get_cart,
        }
        handler = dispatch.get(tool_name)
        if not handler:
            raise ValueError(f"Unknown Food MCP tool: {tool_name}")
        return await handler(params)

    # -------------------------------------------------------------------------
    # Public interface — these are what orchestrator.py calls
    # -------------------------------------------------------------------------

    async def get_addresses(self, access_token: str | None = None) -> dict[str, Any]:
        """
        Resolve user's saved delivery addresses.

        MUST be called before search_restaurants — Food requires addressId
        not lat/lng (unlike Dineout which uses lat/lng).

        Returns:
            dict with "data" list of addresses, each containing:
              - id (addressId): pass this to search_restaurants
              - label: "Home", "Work" etc.
              - displayText: human-readable address string
        """
        return await self._call_mcp("get_addresses", {}, access_token=access_token)

    async def search_restaurants(
        self,
        address_id: str,
        query: str = "",
        dietary_filters: list[str] | None = None,
        budget_per_head: int = 500,
        health_focus: int = 50,
        access_token: str | None = None,
    ) -> dict[str, Any]:
        """
        Search for food delivery restaurants by addressId.

        IMPORTANT: Uses addressId (not lat/lng) — Food-specific requirement.
        Filter results to availabilityStatus: "OPEN" before presenting.

        Args:
            address_id: from get_addresses() response — delivery location
            query: search query e.g. "biryani", "fine dining", "vegetarian"
            dietary_filters: e.g. ["Veg", "Jain"] — filters menus accordingly
            budget_per_head: per-person budget in INR for food delivery portion
            health_focus: 0-100, shapes how results are ranked

        Returns:
            dict with "data.restaurants" list, each containing:
              - id, name, cuisine, rating, deliveryTimeMinutes, priceForTwo
              - topDishes: list of recommended dishes with prices
              - offers: active offers (check requiresOnlinePayment for COD eligibility)
              - availabilityStatus: only recommend "OPEN" restaurants
        """
        return await self._call_mcp(
            "search_restaurants",
            {
                "addressId": address_id,
                "query": query,
                "dietary_filters": dietary_filters or [],
                "budget_per_head": budget_per_head,
                "health_focus": health_focus,
            },
            access_token=access_token,
        )

    async def get_restaurant_menu(self, restaurant_id: str) -> dict[str, Any]:
        """
        Fetch the full menu for a specific restaurant.

        Used after search_restaurants() when the AI wants to recommend
        specific dishes rather than just the restaurant.

        Returns categories, items, variants, and add-ons.
        """
        return await self._call_mcp(
            "get_restaurant_menu", {"restaurantId": restaurant_id}
        )

    async def search_menu(self, query: str, address_id: str) -> dict[str, Any]:
        """Keyword search within/across restaurants at an address."""
        return await self._call_mcp(
            "search_menu", {"query": query, "addressId": address_id}
        )

    async def fetch_food_coupons(self) -> dict[str, Any]:
        """
        Fetch available coupons.
        In v1, filter to requiresOnlinePayment=False (COD only).
        """
        return await self._call_mcp("fetch_food_coupons", {})

    async def get_food_cart(self) -> dict[str, Any]:
        """
        Get current cart contents and total.
        Check total <= 1000 before place_food_order (v1 hard cap).
        """
        return await self._call_mcp("get_food_cart", {})

    # -------------------------------------------------------------------------
    # Mock responses — mirror real Swiggy MCP response shapes exactly.
    # Replace internals only when real MCP is connected.
    # Field names match real API: addressId, availabilityStatus, requiresOnlinePayment etc.
    # -------------------------------------------------------------------------

    async def _mock_get_addresses(self, params: dict) -> dict:
        """Mock saved addresses response."""
        return {
            "data": [
                {
                    "id": "addr_001",
                    "label": "Home",
                    "displayText": "123 Hazratganj, Lucknow, UP 226001",
                },
                {
                    "id": "addr_002",
                    "label": "Work",
                    "displayText": "Vibhuti Khand, Gomti Nagar, Lucknow",
                },
            ]
        }

    async def _mock_search_restaurants(self, params: dict) -> dict:
        """
        Mock response mirroring Swiggy Food MCP search_restaurants output.
        Restaurant names, dishes, and prices are realistic for Indian context.
        Field names match real API shapes.
        """
        dietary = params.get("dietary_filters", [])
        is_veg = "Veg" in dietary or "Jain" in dietary
        health_focus = params.get("health_focus", 50)
        budget = params.get("budget_per_head", 500)

        restaurants = [
            {
                "id": "rest_001",
                "name": "Meghana Foods" if not is_veg else "Saravanaa Bhavan",
                "cuisine": "Andhra" if not is_veg else "South Indian",
                "rating": 4.5,
                "deliveryTimeMinutes": 35,
                "priceForTwo": min(budget * 2, 600),
                "distanceKm": 1.2,
                "availabilityStatus": "OPEN",
                "offers": [
                    {
                        "code": "SWIGGY50",
                        "description": "50% off up to ₹100",
                        "minOrder": 199,
                        "requiresOnlinePayment": False,
                    },
                ],
                "topDishes": [
                    {
                        "name": "Biryani" if not is_veg else "Masala Dosa",
                        "price": 220,
                        "isBestseller": True,
                    },
                    {
                        "name": "Pepper Chicken" if not is_veg else "Pongal",
                        "price": 280,
                        "isBestseller": False,
                    },
                    {"name": "Gulab Jamun", "price": 80, "isBestseller": False},
                ],
            },
            {
                "id": "rest_002",
                "name": "Barbeque Nation" if not is_veg else "Haldiram's",
                "cuisine": "Mughlai" if not is_veg else "North Indian",
                "rating": 4.3,
                "deliveryTimeMinutes": 45,
                "priceForTwo": min(budget * 2, 800),
                "distanceKm": 2.1,
                "availabilityStatus": "OPEN",
                "offers": [
                    {
                        "code": "HDFC10",
                        "description": "10% off with HDFC card",
                        "minOrder": 499,
                        "requiresOnlinePayment": True,
                    },
                ],
                "topDishes": [
                    {
                        "name": "Mutton Seekh Kebab" if not is_veg else "Paneer Tikka",
                        "price": 349,
                        "isBestseller": True,
                    },
                    {"name": "Dal Makhani", "price": 199, "isBestseller": True},
                    {"name": "Butter Naan", "price": 49, "isBestseller": False},
                ],
            },
            {
                "id": "rest_003",
                "name": "Social",
                "cuisine": "Continental",
                "rating": 4.1,
                "deliveryTimeMinutes": 40,
                "priceForTwo": min(budget * 2, 700),
                "distanceKm": 1.8,
                "availabilityStatus": "OPEN",
                "offers": [],
                "topDishes": [
                    {"name": "Loaded Fries", "price": 249, "isBestseller": True},
                    {
                        "name": "Quinoa Bowl"
                        if health_focus > 60
                        else "Pulled Pork Burger",
                        "price": 349,
                        "isBestseller": False,
                    },
                    {"name": "Tiramisu", "price": 199, "isBestseller": False},
                ],
            },
        ]

        return {
            "data": {
                "restaurants": restaurants,
                "addressId": params.get("addressId"),
                "totalResults": len(restaurants),
            }
        }

    async def _mock_get_restaurant_menu(self, params: dict) -> dict:
        """Mock full menu response for a restaurant."""
        return {
            "data": {
                "restaurantId": params["restaurantId"],
                "categories": [
                    {
                        "name": "Starters",
                        "items": [
                            {
                                "id": "item_001",
                                "name": "Paneer Tikka",
                                "price": 249,
                                "isVeg": True,
                            },
                            {
                                "id": "item_002",
                                "name": "Chicken 65",
                                "price": 299,
                                "isVeg": False,
                            },
                        ],
                    },
                    {
                        "name": "Main Course",
                        "items": [
                            {
                                "id": "item_003",
                                "name": "Dal Makhani",
                                "price": 199,
                                "isVeg": True,
                            },
                            {
                                "id": "item_004",
                                "name": "Butter Chicken",
                                "price": 349,
                                "isVeg": False,
                            },
                        ],
                    },
                ],
            }
        }

    async def _mock_search_menu(self, params: dict) -> dict:
        """Mock dish search across restaurants."""
        return {"data": {"dishes": [], "query": params.get("query", "")}}

    async def _mock_fetch_coupons(self, params: dict) -> dict:
        """Mock available coupons. Note requiresOnlinePayment field for COD filtering."""
        return {
            "data": [
                {
                    "code": "SWIGGY50",
                    "description": "50% off up to ₹100",
                    "requiresOnlinePayment": False,
                },
                {
                    "code": "HDFC10",
                    "description": "10% off with HDFC card",
                    "requiresOnlinePayment": True,
                },
            ]
        }

    async def _mock_get_cart(self, params: dict) -> dict:
        """Mock cart contents response."""
        return {"data": {"items": [], "total": 0, "deliveryFee": 25}}
