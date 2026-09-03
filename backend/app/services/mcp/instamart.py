"""
services/mcp/instamart.py — Swiggy Instamart MCP client.

Instamart is Swiggy's 10-minute grocery delivery service.
In Soirée, it handles the "stay in" and "hybrid" event modes —
delivering groceries, snacks, beverages, and party supplies
directly to the event venue.

REAL TOOL NAMES (confirmed from Swiggy docs):
  get_addresses       → resolve delivery address (returns addressId)
  search_products     → find products by addressId + query
  your_go_to_items    → user's frequently ordered SKUs (quick reorder path)
  update_cart         → add/remove items using spinId (variant-level identifier)
  clear_cart          → explicitly clear cart (call before switching address)
  get_cart            → current cart + bill breakdown
  checkout            → place order (Phase 2) — NOT idempotent
  get_orders          → order history (verify checkout on 5xx before retry)
  track_order         → live delivery tracking (ETA 10-20 min)
  create_address      → add new delivery address if user has none

CRITICAL PRODUCTION NOTES:
  - Instamart uses spinId (variant-level identifier), NOT productId for cart
  - Minimum order: ₹99 — check for MIN_ORDER_NOT_MET error
  - Clear cart before switching address to avoid cross-address SKU mismatches
  - checkout is NOT idempotent — on 5xx call get_orders before retrying
  - Check ADDRESS_NOT_SERVICEABLE — Instamart has service area restrictions

CONCEPT: Event-type driven product selection
---------------------------------------------
Unlike Food (where the AI picks restaurants), Instamart requires us
to translate an event type into a product shopping list:

  house_party  → chips, dips, soft drinks, paper cups, napkins, ice
  date         → candles, flowers, chocolates, premium snacks, juice
  birthday     → cake (if not ordered), balloons, decorations, snacks
  corporate    → tea/coffee sachets, biscuits, bottled water, tissues
  family       → cooking ingredients, fresh produce, snacks for kids

The AI planner decides the high-level list; this client fetches
real product IDs and prices from Instamart's catalog.

MCP URL: https://mcp.swiggy.com/im
"""

import asyncio
from typing import Any

from app.services.mcp.base import BaseMCPClient


class InstamartMCPClient(BaseMCPClient):
    """
    Client for Swiggy Instamart MCP server.

    Translates event context into a recommended grocery/supplies cart.
    Mock responses mirror real Instamart product catalog structure.
    Transport (real vs mock) lives in BaseMCPClient.

    Usage:
        client = InstamartMCPClient()
        # Step 1: resolve address
        addresses = await client.get_addresses()
        address_id = addresses["data"][0]["id"]
        # Step 2: search products
        results = await client.search_products(
            address_id=address_id,
            query="candles chocolates",
            event_type="date",
            guest_count=2,
        )
    """

    MCP_URL = "https://mcp.swiggy.com/im"

    async def _mock_dispatch(self, tool_name: str, params: dict) -> dict:
        """Route mock calls to appropriate mock method."""
        # Simulate network latency so async behaviour is realistic in dev
        await asyncio.sleep(0.1)
        dispatch = {
            "get_addresses": self._mock_get_addresses,
            "search_products": self._mock_search_products,
            "your_go_to_items": self._mock_go_to_items,
            "get_cart": self._mock_get_cart,
        }
        handler = dispatch.get(tool_name)
        if not handler:
            raise ValueError(f"Unknown Instamart MCP tool: {tool_name}")
        return await handler(params)

    # -------------------------------------------------------------------------
    # Public interface — these are what orchestrator.py calls
    # -------------------------------------------------------------------------

    async def get_addresses(self, access_token: str | None = None) -> dict[str, Any]:
        """
        Resolve user's saved delivery addresses.
        Same as Food get_addresses — must be called before search_products.

        Returns:
            dict with "data" list of addresses, each containing:
              - id (addressId): pass this to search_products and update_cart
              - label: "Home", "Work" etc.
              - displayText: human-readable address string
        """
        return await self._call_mcp("get_addresses", {}, access_token=access_token)

    async def search_products(
        self,
        address_id: str,
        query: str,
        event_type: str = "friends",
        guest_count: int = 2,
        dietary_tags: list[str] | None = None,
        budget: int | None = None,
        access_token: str | None = None,
    ) -> dict[str, Any]:
        """
        Find grocery products by addressId + query.

        Products are grouped by category and scaled by guest_count.
        E.g. a house_party for 20 people needs more chips than one for 4.

        IMPORTANT: Returns products with variants — use variant.spinId
        (not productId) for cart operations via update_cart.

        Args:
            address_id: from get_addresses() — delivery location
            query: search query shaped by event type e.g. "candles chocolates juice"
            event_type: shapes product selection (house_party, date, corporate etc.)
            guest_count: number of attendees (used to scale quantities)
            dietary_tags: e.g. ["Veg"] — filters out non-veg products
            budget: optional INR cap for the Instamart portion

        Returns:
            dict with "data.categories" list, each containing products with:
              - productId, name, brand
              - variants: list with spinId (use this for cart), price, unit
              - recommendedQty: how many units to buy for this event size
        """
        return await self._call_mcp(
            "search_products",
            {
                "addressId": address_id,
                "query": query,
                "event_type": event_type,
                "guest_count": guest_count,
                "dietary_tags": dietary_tags or [],
                "budget": budget,
            },
            access_token=access_token,
        )

    async def get_go_to_items(
        self, address_id: str, access_token: str | None = None
    ) -> dict[str, Any]:
        """
        User's frequently ordered SKUs — present as one-tap quick reorder.
        Bypass search for returning users who want to quickly restock.

        Args:
            address_id: from get_addresses() — delivery location
            access_token: optional authentication token

        Returns frequently-ordered products with spinId for instant add-to-cart.
        """
        return await self._call_mcp(
            "your_go_to_items", {"addressId": address_id}, access_token=access_token
        )

    async def get_cart(self, access_token: str | None = None) -> dict[str, Any]:
        """
        Get current cart contents with bill breakdown and payment methods.
        Check for MIN_ORDER_NOT_MET (₹99 minimum) before checkout.
        """
        return await self._call_mcp("get_cart", {}, access_token=access_token)

    # -------------------------------------------------------------------------
    # Mock responses — mirror real Instamart MCP response shapes.
    # Key change from original: products now have variants with spinId
    # (the real Instamart cart identifier) instead of product_id.
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
            ]
        }

    async def _mock_search_products(self, params: dict) -> dict:
        """
        Mock Instamart product catalog response.
        Products are grouped by category, quantities scaled to guest count.
        Each product has variants with spinId — the real cart identifier.
        """
        event_type = params.get("event_type", "friends")
        guests = params.get("guest_count", 4)

        # Scale quantities to guest count
        # Rule of thumb: 1 chips bag per 3 guests, 1 drink per guest etc.
        chips_qty = max(1, guests // 3)
        drinks_qty = max(2, guests // 2)

        # Product catalog varies by event type
        event_products = {
            "house_party": [
                {
                    "category": "Snacks",
                    "items": [
                        {
                            "productId": "p001",
                            "name": "Lay's Classic Salted",
                            "brand": "Lay's",
                            "variants": [
                                {"spinId": "sp001", "price": 40, "unit": "73g"}
                            ],
                            "recommendedQty": chips_qty,
                        },
                        {
                            "productId": "p002",
                            "name": "Kurkure Masala Munch",
                            "brand": "Kurkure",
                            "variants": [
                                {"spinId": "sp002", "price": 30, "unit": "90g"}
                            ],
                            "recommendedQty": chips_qty,
                        },
                        {
                            "productId": "p003",
                            "name": "Bikaji Bhujia",
                            "brand": "Bikaji",
                            "variants": [
                                {"spinId": "sp003", "price": 60, "unit": "200g"}
                            ],
                            "recommendedQty": max(1, guests // 5),
                        },
                    ],
                },
                {
                    "category": "Beverages",
                    "items": [
                        {
                            "productId": "p010",
                            "name": "Coca-Cola",
                            "brand": "Coca-Cola",
                            "variants": [
                                {"spinId": "sp010", "price": 45, "unit": "1.25L"}
                            ],
                            "recommendedQty": drinks_qty,
                        },
                        {
                            "productId": "p011",
                            "name": "Sprite",
                            "brand": "Sprite",
                            "variants": [
                                {"spinId": "sp011", "price": 45, "unit": "1.25L"}
                            ],
                            "recommendedQty": drinks_qty,
                        },
                        {
                            "productId": "p012",
                            "name": "Frooti Mango Drink",
                            "brand": "Parle Agro",
                            "variants": [
                                {"spinId": "sp012", "price": 20, "unit": "200ml"}
                            ],
                            "recommendedQty": guests,
                        },
                    ],
                },
                {
                    "category": "Party Supplies",
                    "items": [
                        {
                            "productId": "p020",
                            "name": "Paper Cups",
                            "brand": "Chuk",
                            "variants": [
                                {"spinId": "sp020", "price": 99, "unit": "pack of 50"}
                            ],
                            "recommendedQty": 1,
                        },
                        {
                            "productId": "p021",
                            "name": "Napkins",
                            "brand": "Tissues Plus",
                            "variants": [
                                {"spinId": "sp021", "price": 49, "unit": "pack of 100"}
                            ],
                            "recommendedQty": 1,
                        },
                    ],
                },
            ],
            "date": [
                {
                    "category": "Ambience",
                    "items": [
                        {
                            "productId": "p030",
                            "name": "Tealight Candles",
                            "brand": "Hosley",
                            "variants": [
                                {"spinId": "sp030", "price": 149, "unit": "pack of 12"}
                            ],
                            "recommendedQty": 1,
                        },
                        {
                            "productId": "p031",
                            "name": "Rose Petals",
                            "brand": "Fresh Flowers",
                            "variants": [
                                {"spinId": "sp031", "price": 99, "unit": "pack"}
                            ],
                            "recommendedQty": 1,
                        },
                    ],
                },
                {
                    "category": "Gourmet Snacks",
                    "items": [
                        {
                            "productId": "p032",
                            "name": "Ferrero Rocher",
                            "brand": "Ferrero",
                            "variants": [
                                {"spinId": "sp032", "price": 399, "unit": "16 pieces"}
                            ],
                            "recommendedQty": 1,
                        },
                        {
                            "productId": "p033",
                            "name": "Pringles Original",
                            "brand": "Pringles",
                            "variants": [
                                {"spinId": "sp033", "price": 199, "unit": "107g"}
                            ],
                            "recommendedQty": 1,
                        },
                    ],
                },
                {
                    "category": "Beverages",
                    "items": [
                        {
                            "productId": "p034",
                            "name": "Raw Pressery Apple Juice",
                            "brand": "Raw Pressery",
                            "variants": [
                                {"spinId": "sp034", "price": 99, "unit": "250ml"}
                            ],
                            "recommendedQty": 2,
                        },
                    ],
                },
            ],
            "corporate": [
                {
                    "category": "Hot Beverages",
                    "items": [
                        {
                            "productId": "p040",
                            "name": "Nescafé Classic",
                            "brand": "Nestlé",
                            "variants": [
                                {"spinId": "sp040", "price": 245, "unit": "100g jar"}
                            ],
                            "recommendedQty": 1,
                        },
                        {
                            "productId": "p041",
                            "name": "Tata Tea Gold",
                            "brand": "Tata",
                            "variants": [
                                {"spinId": "sp041", "price": 159, "unit": "250g pack"}
                            ],
                            "recommendedQty": 1,
                        },
                        {
                            "productId": "p042",
                            "name": "Sugar Sachets",
                            "brand": "Generic",
                            "variants": [
                                {"spinId": "sp042", "price": 49, "unit": "pack of 50"}
                            ],
                            "recommendedQty": 1,
                        },
                    ],
                },
                {
                    "category": "Biscuits & Snacks",
                    "items": [
                        {
                            "productId": "p043",
                            "name": "Britannia Good Day",
                            "brand": "Britannia",
                            "variants": [
                                {"spinId": "sp043", "price": 35, "unit": "pack of 5"}
                            ],
                            "recommendedQty": max(1, guests // 5),
                        },
                        {
                            "productId": "p044",
                            "name": "Parle-G",
                            "brand": "Parle",
                            "variants": [
                                {"spinId": "sp044", "price": 10, "unit": "pack"}
                            ],
                            "recommendedQty": max(2, guests // 4),
                        },
                    ],
                },
                {
                    "category": "Water",
                    "items": [
                        {
                            "productId": "p045",
                            "name": "Bisleri Water",
                            "brand": "Bisleri",
                            "variants": [
                                {"spinId": "sp045", "price": 20, "unit": "1L bottle"}
                            ],
                            "recommendedQty": guests,
                        },
                    ],
                },
            ],
        }

        # Default to house_party products if event type not specifically mapped
        products = event_products.get(event_type, event_products["house_party"])

        # Calculate estimated total using first variant price × recommended qty
        total = sum(
            item["variants"][0]["price"] * item["recommendedQty"]
            for category in products
            for item in category["items"]
        )

        return {
            "data": {
                "categories": products,
                "eventType": event_type,
                "guestCount": guests,
                "estimatedTotal": total,
                "deliveryEstimateMinutes": 15,  # Instamart's 10-15 min promise
            }
        }

    async def _mock_go_to_items(self, params: dict) -> dict:
        """Mock frequently ordered items response."""
        return {"data": {"products": []}}

    async def _mock_get_cart(self, params: dict) -> dict:
        """Mock cart contents response."""
        return {"data": {"items": [], "total": 0, "deliveryFee": 25}}
