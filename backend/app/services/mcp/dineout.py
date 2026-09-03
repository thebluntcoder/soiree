"""
services/mcp/dineout.py — Swiggy Dineout MCP client.

Dineout is Swiggy's table reservation service — the "going out" half
of Soirée's hybrid mode. It handles:
  - Finding restaurants suitable for dine-in (different from delivery)
  - Checking real-time slot availability
  - Booking a table for a specific time and party size

REAL TOOL NAMES (confirmed from Swiggy docs):
  get_saved_locations       → resolve user's saved locations (returns lat/lng)
  search_restaurants_dineout → find dine-in restaurants by lat/lng + query
  get_restaurant_details    → ratings, amenities, menu images, Dineout deals
  get_available_slots       → 7-day forward availability by date + guestCount
  book_table                → make a reservation (Phase 2) — NOT idempotent
  get_booking_status        → check reservation status by bookingId

CRITICAL DIFFERENCES FROM FOOD/INSTAMART:
  - Dineout uses lat/lng from get_saved_locations (NOT addressId)
  - Slots have slotId — pass slotId to book_table, not a time string
  - book_table is NOT idempotent — on 5xx call get_booking_status before retrying
  - Filter to restaurants where availability is "AVAILABLE"
  - Slots are 7-day forward, broken into breakfast/lunch/dinner bands
  - All times are IST

CONCEPT: Dineout vs Food search — why they're different clients
----------------------------------------------------------------
Although both search "restaurants," they serve different purposes:
  - Food search: delivery radius, packaging quality, delivery time matter
  - Dineout search: ambience, parking, occasion-fit, dress code matter

The AI planner uses different prompting strategies for each:
  - Food: "best biryani under ₹300 that delivers in 30 min"
  - Dineout: "romantic rooftop restaurant for 2, anniversary vibe, ₹2000 budget"

CONCEPT: Slot availability is time-sensitive
---------------------------------------------
Unlike food menus (cached 30min), slot availability changes by the minute
as other users book. We NEVER cache slot data — always fetch live.
The slotId from get_available_slots must be passed directly to book_table.

MCP URL: https://mcp.swiggy.com/dineout
"""

import asyncio
from typing import Any

from app.services.mcp.base import BaseMCPClient


class DineoutMCPClient(BaseMCPClient):
    """
    Client for Swiggy Dineout MCP server.

    Handles restaurant discovery and table reservations for dine-in events.
    Slot availability is always fetched live — never cached.

    KEY DIFFERENCE: Dineout uses lat/lng from get_saved_locations(),
    while Food/Instamart use addressId from get_addresses().
    These are different scopes — never mix them.

    Usage:
        client = DineoutMCPClient()
        # Step 1: resolve location (lat/lng, not addressId)
        locations = await client.get_saved_locations()
        lat = locations["data"][0]["lat"]
        lng = locations["data"][0]["lng"]
        # Step 2: search restaurants
        results = await client.search_restaurants(
            lat=lat, lng=lng,
            guest_count=4,
            event_type="birthday",
        )
        # Step 3: check slots (never cache this)
        slots = await client.get_available_slots(
            restaurant_id="dine_001",
            date="2026-05-10",
            guest_count=4,
        )
        # Step 4: book using slotId (not time string)
        # booking = await client.book_table(
        #     restaurant_id="dine_001",
        #     slot_id=slots["data"]["slots"][0]["slotId"],
        #     guest_count=4,
        # )
    """

    MCP_URL = "https://mcp.swiggy.com/dineout"

    async def _mock_dispatch(self, tool_name: str, params: dict) -> dict:
        """Route mock calls to appropriate mock method."""
        # Simulate network latency so async behaviour is realistic in dev
        await asyncio.sleep(0.1)
        dispatch = {
            "get_saved_locations": self._mock_get_saved_locations,
            "search_restaurants_dineout": self._mock_search_restaurants,
            "get_restaurant_details": self._mock_get_restaurant_details,
            "get_available_slots": self._mock_get_available_slots,
        }
        handler = dispatch.get(tool_name)
        if not handler:
            raise ValueError(f"Unknown Dineout MCP tool: {tool_name}")
        return await handler(params)

    # -------------------------------------------------------------------------
    # Public interface — these are what orchestrator.py calls
    # -------------------------------------------------------------------------

    async def get_saved_locations(
        self, access_token: str | None = None
    ) -> dict[str, Any]:
        """
        Resolve user's saved locations — returns lat/lng.

        MUST be called before search_restaurants_dineout.
        Dineout uses lat/lng, NOT addressId (unlike Food/Instamart).

        Returns:
            dict with "data" list of locations, each containing:
              - id, label: "Home"/"Work" etc.
              - lat, lng: coordinates for Dineout search
              - displayText: human-readable location string
        """
        return await self._call_mcp(
            "get_saved_locations", {}, access_token=access_token
        )

    async def search_restaurants(
        self,
        lat: float,
        lng: float,
        query: str = "",
        guest_count: int = 2,
        dietary_filters: list[str] | None = None,
        event_type: str = "date",
        budget_per_head: int = 1000,
        start_hour: int = 20,
        access_token: str | None = None,
        address_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Search for dine-in restaurants by lat/lng (NOT addressId).
        Filter results to availability: "AVAILABLE" before presenting.

        Args:
            lat, lng: from get_saved_locations() or device GPS — Dineout-specific
            query: search query e.g. "italian", "romantic rooftop"
            guest_count: party size (used for table availability check)
            dietary_filters: cuisine/dietary constraints
            event_type: shapes ambience filtering (romantic/corporate/casual)
            budget_per_head: per-person spend limit in INR
            start_hour: preferred start time in 24h (used for slot search)

        Returns:
            dict with "data.restaurants" list, each containing:
              - id, name, cuisine, rating, costForTwo, ambience tags
              - availableSlots: list of {slotId, time, band} — use slotId for booking
              - offers: active Dineout offers (pre-payment discounts etc.)
              - availability: only present "AVAILABLE" restaurants
        """
        params = {
            "query": query,
            "guestCount": guest_count,
            # "dietary_filters": dietary_filters or [],
            # "event_type": event_type,
            # "budget_per_head": budget_per_head,
            # "start_hour": start_hour,
        }
        # Use address_id if available (real API), otherwise lat/lng (mock)
        if address_id:
            params["addressId"] = address_id
        else:
            params["lat"] = lat
            params["lng"] = lng

        return await self._call_mcp(
            "search_restaurants_dineout",
            params,
            access_token=access_token,
        )

    async def get_available_slots(
        self,
        restaurant_id: str,
        date: str,
        guest_count: int,
    ) -> dict[str, Any]:
        """
        Fetch real-time slot availability for a specific restaurant.

        Returns 7-day forward availability broken into breakfast/lunch/dinner bands.
        Each slot has a slotId — pass this to book_table (NOT the time string).

        IMPORTANT: Never cache this — slots change in real time as others book.
        All times are in IST.

        Args:
            restaurant_id: from search_restaurants response
            date: ISO date string e.g. "2026-05-10"
            guest_count: number of guests (affects table availability)

        Returns:
            dict with "data.slots" list: [{slotId, time, band, available}]
        """
        return await self._call_mcp(
            "get_available_slots",
            {
                "restaurantId": restaurant_id,
                "date": date,
                "guestCount": guest_count,
            },
        )

    async def get_restaurant_details(self, restaurant_id: str) -> dict[str, Any]:
        """
        Fetch full restaurant details: ratings, amenities, photos, Dineout deals.
        Used when the AI needs more context to write a compelling recommendation.
        """
        return await self._call_mcp(
            "get_restaurant_details", {"restaurantId": restaurant_id}
        )

    # -------------------------------------------------------------------------
    # Mock responses — mirror real Dineout MCP response shapes.
    # Key changes from original:
    #   - Uses lat/lng params instead of location string
    #   - Slots now have slotId (not just time string) for booking
    #   - availability field is "AVAILABLE" (not "open")
    # -------------------------------------------------------------------------

    async def _mock_get_saved_locations(self, params: dict) -> dict:
        """Mock saved locations with real Lucknow coordinates."""
        return {
            "data": [
                {
                    "id": "loc_001",
                    "label": "Home",
                    "lat": 26.8467,
                    "lng": 80.9462,
                    "displayText": "Hazratganj, Lucknow",
                },
                {
                    "id": "loc_002",
                    "label": "Work",
                    "lat": 26.8631,
                    "lng": 80.9915,
                    "displayText": "Gomti Nagar, Lucknow",
                },
            ]
        }

    async def _mock_search_restaurants(self, params: dict) -> dict:
        """
        Mock Dineout search response.
        Restaurants are occasion-appropriate with realistic Indian pricing.
        Slots now include slotId for booking (real API requirement).
        """
        event_type = params.get("event_type", "date")
        budget = params.get("budget_per_head", 1000)
        guests = params.get("guestCount", 2)
        hour = params.get("start_hour", 20)

        # Slots include slotId — this is what gets passed to book_table
        slot_times = (
            [
                {
                    "slotId": f"slot_{hour - 1}30",
                    "time": f"{hour - 1}:30 PM",
                    "band": "dinner",
                    "available": True,
                },
                {
                    "slotId": f"slot_{hour}00",
                    "time": f"{hour}:00 PM",
                    "band": "dinner",
                    "available": True,
                },
                {
                    "slotId": f"slot_{hour}30",
                    "time": f"{hour}:30 PM",
                    "band": "dinner",
                    "available": True,
                },
            ]
            if hour > 12
            else [
                {
                    "slotId": "slot_default",
                    "time": f"{hour}:00 PM",
                    "band": "dinner",
                    "available": True,
                }
            ]
        )

        restaurants = [
            {
                "id": "dine_001",
                "name": "Farzi Cafe"
                if event_type in ("date", "friends")
                else "The Leela Terrace",
                "cuisine": "Modern Indian",
                "rating": 4.6,
                "costForTwo": min(budget * 2, 1800),
                "availability": "AVAILABLE",
                "ambience": ["Rooftop", "Romantic", "Live Music"]
                if event_type == "date"
                else ["Casual", "Trendy"],
                "distanceKm": 1.5,
                "dressCode": "Smart Casual",
                "knownFor": [
                    "Molecular gastronomy",
                    "Craft cocktails",
                    "Instagram-worthy plating",
                ],
                "availableSlots": slot_times[:3],
                "offers": [
                    {
                        "type": "pre_booking",
                        "description": "15% off on pre-booking",
                        "code": "EARLYBIRD15",
                    },
                ],
            },
            {
                "id": "dine_002",
                "name": "Punjab Grill"
                if event_type == "family"
                else "Smoke House Deli",
                "cuisine": "North Indian" if event_type == "family" else "European",
                "rating": 4.4,
                "costForTwo": min(budget * 2, 2200),
                "availability": "AVAILABLE",
                "ambience": ["Family-friendly", "Spacious"]
                if event_type == "family"
                else ["Intimate", "Cosy"],
                "distanceKm": 2.3,
                "dressCode": "Casual",
                "knownFor": ["Dal Makhani", "Tandoori platters"]
                if event_type == "family"
                else ["Wood-fired pizza", "All-day brunch"],
                "availableSlots": slot_times[:2],
                "offers": [
                    {
                        "type": "discount",
                        "description": "20% off for groups of 4+",
                        "code": "GROUP20",
                    },
                ]
                if guests >= 4
                else [],
            },
            {
                "id": "dine_003",
                "name": "The Black Sheep Bistro",
                "cuisine": "Contemporary",
                "rating": 4.5,
                "costForTwo": min(budget * 2, 2500),
                "availability": "AVAILABLE",
                "ambience": ["Chic", "Intimate", "Wine bar"],
                "distanceKm": 3.1,
                "dressCode": "Smart Casual",
                "knownFor": [
                    "Extensive wine list",
                    "Chef's tasting menu",
                    "Housemade pasta",
                ],
                "availableSlots": slot_times,
                "offers": [],
            },
        ]

        return {
            "data": {
                "restaurants": restaurants,
                "lat": params.get("lat"),
                "lng": params.get("lng"),
                "totalResults": len(restaurants),
                "note": "Slots fetched live — availability may change",
            }
        }

    async def _mock_get_available_slots(self, params: dict) -> dict:
        """
        Mock slot availability for a specific restaurant.
        Slots include slotId — the real booking identifier.
        """
        return {
            "data": {
                "restaurantId": params["restaurantId"],
                "date": params.get("date"),
                "guestCount": params.get("guestCount"),
                "slots": [
                    {
                        "slotId": "slot_1930",
                        "time": "7:30 PM",
                        "band": "dinner",
                        "available": True,
                    },
                    {
                        "slotId": "slot_2000",
                        "time": "8:00 PM",
                        "band": "dinner",
                        "available": True,
                    },
                    {
                        "slotId": "slot_2030",
                        "time": "8:30 PM",
                        "band": "dinner",
                        "available": True,
                    },
                    {
                        "slotId": "slot_2100",
                        "time": "9:00 PM",
                        "band": "dinner",
                        "available": False,
                    },
                ],
                "note": "Slots are live — book quickly to confirm",
            }
        }

    async def _mock_get_restaurant_details(self, params: dict) -> dict:
        """Mock detailed restaurant info including Dineout-exclusive deals."""
        return {
            "data": {
                "id": params["restaurantId"],
                "description": "A contemporary dining experience with seasonal ingredients and bold flavours.",
                "amenities": [
                    "Valet parking",
                    "Private dining room",
                    "Wheelchair accessible",
                ],
                "dineoutDeals": [
                    "15% off on pre-booking",
                    "Complimentary dessert on birthdays",
                ],
                "parking": True,
                "acceptsLargeGroups": True,
            }
        }
