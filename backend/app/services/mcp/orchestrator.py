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
Serial:   Food(300ms) → Instamart(200ms) → Dineout(250ms) = 750ms total
Parallel: all three fire at once → done in ~300ms (longest one wins)
That's 2.5x faster with zero extra complexity.

CONCEPT: Address scopes differ per server
------------------------------------------
  Food/Instamart → addressId from get_addresses()
  Dineout        → lat/lng from get_saved_locations()
Never mix these scopes.

CONCEPT: Venue mode → which MCPs are called
--------------------------------------------
  out    → Dineout only
  home   → Food (full meal) + Instamart (groceries/supplies)
  hybrid → Dineout (main meal) + Food (celebration items e.g. cake from bakery)
           + Instamart (ambience: candles, flowers, soft drinks)

NOTE: Instamart does NOT have cakes. Cakes come from Swiggy Food (bakeries).
Instamart = candles, flowers, drinks, chips, decorations.

CONCEPT: Notes drive MCP search queries
-----------------------------------------
If user writes "order cake" in notes → Food searches bakeries
If user writes "flowers" → Instamart searches flowers
This ensures MCP returns relevant products, not just generic results.

CONCEPT: Alcohol preference
-----------------------------
  yes → Dineout: rooftop bars, wine lists; Instamart: beer/wine
  no  → Dineout: family restaurants; Instamart: soft drinks, juices
  any → no filter applied
"""

import asyncio
from typing import Any
from app.services.mcp.food import FoodMCPClient
from app.services.mcp.instamart import InstamartMCPClient
from app.services.mcp.dineout import DineoutMCPClient


DEFAULT_MOCK_ADDRESS_ID = "addr_001"
DEFAULT_MOCK_LOCATION = {"lat": 26.8467, "lng": 80.9462}


class MCPOrchestrator:
    """
    Coordinates parallel calls to all three Swiggy MCP servers.
    Stateless — safe for concurrent use across requests.
    """

    def __init__(self):
        self.food = FoodMCPClient()
        self.instamart = InstamartMCPClient()
        self.dineout = DineoutMCPClient()

    async def resolve_addresses(
        self, access_token: str | None = None
    ) -> tuple[str, dict]:
        """
        Resolve addressId (Food/Instamart) and lat/lng (Dineout).

        Real Swiggy MCP returns text responses that need parsing.
        Mock mode returns hardcoded defaults immediately.
        """
        if not access_token:
            return DEFAULT_MOCK_ADDRESS_ID, DEFAULT_MOCK_LOCATION

        addresses_result, locations_result = await asyncio.gather(
            self.food.get_addresses(access_token=access_token),
            self.dineout.get_saved_locations(access_token=access_token),
            return_exceptions=True,
        )
        import logging

        logging.warning(f"locations_result raw: {str(locations_result)[:800]}")

        address_id = DEFAULT_MOCK_ADDRESS_ID
        if not isinstance(addresses_result, Exception):
            address_id = _parse_address_id(addresses_result, preferred_city="Lucknow")

        # Dineout also uses address ID (same as Food/Instamart)
        # get_saved_locations returns same format as get_addresses
        dineout_address_id = DEFAULT_MOCK_ADDRESS_ID
        if not isinstance(locations_result, Exception):
            dineout_address_id = _parse_address_id(
                locations_result, preferred_city="Lucknow"
            )

        logging.info(
            f"Resolved food/instamart addressId={address_id}, dineout addressId={dineout_address_id}"
        )
        # Return dineout_address_id as the "location" dict for compatibility
        return address_id, {
            "address_id": dineout_address_id,
            "lat": DEFAULT_MOCK_LOCATION["lat"],
            "lng": DEFAULT_MOCK_LOCATION["lng"],
        }

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
        notes: str | None = None,
        alcohol_preference: str = "any",
        access_token: str | None = None,
    ) -> dict[str, Any]:
        """
        Resolve addresses then fire all relevant MCP calls in parallel.

        Args:
            location:           city/area text (display only)
            event_type:         date/friends/birthday/corporate/house_party/family
            venue_mode:         out/home/hybrid
            dietary_tags:       group-level dietary restrictions
            guest_count:        total people including host
            budget:             total INR across all services
            start_hour:         24h float (20.5 = 8:30 PM)
            health_focus:       0-100 wellness slider
            lat/lng:            device GPS — improves Dineout accuracy
            notes:              free text — drives MCP search queries
            alcohol_preference: yes/no/any — filters restaurants and Instamart

        Returns:
            {food, instamart, dineout, venue_mode, budget_split, coordinates}
        """
        budget_split = self._calculate_budget_split(budget, venue_mode)
        address_id, saved_location = await self.resolve_addresses(
            access_token=access_token
        )

        # Use device GPS if provided (more accurate than saved location)
        dineout_lat = lat or saved_location.get("lat", DEFAULT_MOCK_LOCATION["lat"])
        dineout_lng = lng or saved_location.get("lng", DEFAULT_MOCK_LOCATION["lng"])
        dineout_address_id = saved_location.get("address_id", DEFAULT_MOCK_ADDRESS_ID)

        tasks: list[tuple[str, Any]] = []

        # ── Food delivery ────────────────────────────────────────────
        # home: full meal delivery
        # hybrid: celebration items only (cake from bakery, specific dishes)
        if venue_mode in ("home", "hybrid"):
            food_query = _food_query(event_type, dietary_tags, notes, venue_mode)
            tasks.append(
                (
                    "food",
                    self.food.search_restaurants(
                        address_id=address_id,
                        query=food_query,
                        dietary_filters=dietary_tags,
                        budget_per_head=budget_split["food"] // max(guest_count, 1),
                        health_focus=health_focus,
                        access_token=access_token,
                    ),
                )
            )

        # ── Instamart supplies ────────────────────────────────────────
        # home: groceries + supplies
        # hybrid: ambience only (candles, flowers, soft drinks)
        # NOTE: Instamart does NOT have cakes — use Food for cakes
        if venue_mode in ("home", "hybrid"):
            instamart_query = _instamart_query(event_type, notes, alcohol_preference)
            tasks.append(
                (
                    "instamart",
                    self.instamart.search_products(
                        address_id=address_id,
                        query=instamart_query,
                        event_type=event_type,
                        guest_count=guest_count,
                        dietary_tags=dietary_tags,
                        budget=budget_split["instamart"],
                        access_token=access_token,
                    ),
                )
            )

        # ── Dineout reservations ──────────────────────────────────────
        # out + hybrid: restaurant search with alcohol preference
        if venue_mode in ("out", "hybrid"):
            dineout_query = _dineout_query(
                event_type, dietary_tags, alcohol_preference, notes
            )
            tasks.append(
                (
                    "dineout",
                    self.dineout.search_restaurants(
                        lat=dineout_lat,
                        lng=dineout_lng,
                        address_id=dineout_address_id if access_token else None,
                        query=dineout_query,
                        guest_count=guest_count,
                        dietary_filters=dietary_tags,
                        event_type=str(event_type).split(".")[-1]
                        if hasattr(event_type, "value")
                        else event_type,
                        budget_per_head=budget_split["dineout"] // max(guest_count, 1),
                        start_hour=int(start_hour),
                        access_token=access_token,
                    ),
                )
            )

        service_names = [name for name, _ in tasks]
        coroutines = [coro for _, coro in tasks]
        results = await asyncio.gather(*coroutines, return_exceptions=True)

        context = self._process_results(service_names, results)
        context["venue_mode"] = venue_mode
        context["budget_split"] = budget_split
        context["resolved_location"] = location
        context["coordinates"] = {"lat": dineout_lat, "lng": dineout_lng}
        context["alcohol_preference"] = alcohol_preference
        context["notes"] = notes

        return context

    def _calculate_budget_split(self, total: int, venue_mode: str) -> dict[str, int]:
        """
        Split total budget across Swiggy services.

        out:    100% Dineout
        home:   70% Food + 30% Instamart
        hybrid: 60% Dineout + 20% Food (celebration items) + 20% Instamart (ambience)

        Hybrid Food budget is intentionally small — enough for cake/dessert,
        NOT a full meal (user is already eating at the restaurant).
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
                "dineout": int(total * 0.60),
                "food": int(total * 0.20),  # small — cake/dessert only
                "instamart": int(total * 0.20),
            }

    def _process_results(
        self,
        service_names: list[str],
        results: list[Any],
    ) -> dict[str, Any]:
        """
        Map gather() results to service names.
        Failed services return error dict — graceful degradation.
        """
        context: dict[str, Any] = {"food": None, "instamart": None, "dineout": None}
        for service_name, result in zip(service_names, results):
            if isinstance(result, Exception):
                context[service_name] = {
                    "error": str(result),
                    "data": [],
                    "note": f"{service_name} data unavailable — showing partial plan",
                }
            else:
                context[service_name] = result
        return context


# ── Search query builders ────────────────────────────────────────────────────


def _food_query(
    event_type: str,
    dietary_tags: list[str],
    notes: str | None,
    venue_mode: str,
) -> str:
    """
    Build Food MCP search query.

    In hybrid mode: prioritise celebration items from notes (cake, dessert)
    In home mode: full meal based on event type and dietary preference

    IMPORTANT: In hybrid mode, user is dining OUT — Food order is for
    specific items only (birthday cake from bakery, dessert, etc.)
    NOT a full meal.
    """
    # Check notes for specific items first
    if notes:
        notes_lower = notes.lower()
        specific = []
        if any(w in notes_lower for w in ["cake", "birthday cake", "pastry"]):
            specific.append("birthday cake bakery")
        if any(w in notes_lower for w in ["dessert", "sweet", "mithai"]):
            specific.append("dessert sweets")
        if any(
            w in notes_lower
            for w in ["pizza", "burger", "biryani", "chinese", "italian"]
        ):
            for food in ["pizza", "burger", "biryani", "chinese", "italian"]:
                if food in notes_lower:
                    specific.append(food)
        if specific:
            return " ".join(specific)

    # Hybrid without specific notes — search bakeries for celebration items
    if venue_mode == "hybrid":
        celebration_queries = {
            "birthday": "birthday cake bakery dessert",
            "date": "dessert bakery chocolates",
            "anniversary": "cake bakery dessert",
            "friends": "dessert snacks",
            "house_party": "snacks dessert",
            "family": "sweets mithai dessert",
            "corporate": "dessert snacks",
        }
        return celebration_queries.get(event_type, "dessert bakery")

    # Home mode — full meal
    if "Veg" in dietary_tags or "Jain" in dietary_tags:
        return "vegetarian"

    full_meal_queries = {
        "date": "fine dining romantic",
        "birthday": "celebration",
        "corporate": "healthy lunch",
        "house_party": "snacks finger food",
        "family": "family meals",
        "friends": "popular casual",
    }
    return full_meal_queries.get(event_type, "popular")


def _instamart_query(
    event_type: str,
    notes: str | None,
    alcohol_preference: str = "any",
) -> str:
    """
    Build Instamart search query.

    IMPORTANT: Instamart does NOT have cakes or bakery items.
    Instamart = candles, flowers, soft drinks, chips, decorations, party supplies.

    Alcohol preference affects drink suggestions:
      yes → beer, wine (where available)
      no  → soft drinks, juices, mocktails
      any → both
    """
    # Extract ambience keywords from notes
    if notes:
        notes_lower = notes.lower()
        extras = []
        if any(w in notes_lower for w in ["candle", "tealight"]):
            extras.append("candles tealight")
        if any(w in notes_lower for w in ["flower", "rose", "bouquet"]):
            extras.append("flowers roses")
        if any(w in notes_lower for w in ["balloon", "decoration", "decor"]):
            extras.append("balloons decoration")
        if any(w in notes_lower for w in ["chocolate"]):
            extras.append("chocolates")
        if extras:
            return " ".join(extras)

    # Base query from event type
    base_queries = {
        "date": "candles rose petals chocolates juice",
        "birthday": "candles balloons decoration flowers",
        "corporate": "coffee tea biscuits water",
        "house_party": "chips snacks",
        "family": "snacks juice beverages",
        "friends": "chips snacks",
    }
    base = base_queries.get(event_type, "snacks beverages")

    # Add drink preference
    if alcohol_preference == "yes":
        base += " beer wine"
    elif alcohol_preference == "no":
        base += " soft drinks juice"
    else:
        base += " beverages drinks"

    return base


def _dineout_query(
    event_type: str,
    dietary_tags: list[str],
    alcohol_preference: str = "any",
    notes: str | None = None,
) -> str:
    """
    Build Dineout search query.

    Priority:
    1. Explicit cuisine from user notes
    2. Veg/Jain dietary restriction
    3. Alcohol preference
    4. Empty string — return all nearby, Claude picks best for occasion
    """
    # Priority 1 — explicit cuisine from notes
    if notes:
        notes_lower = notes.lower()
        cuisines = [
            "chinese",
            "italian",
            "continental",
            "south indian",
            "north indian",
            "mughlai",
            "thai",
            "japanese",
            "mexican",
            "mediterranean",
            "french",
            "seafood",
            "punjabi",
            "bengali",
            "gujarati",
            "rajasthani",
            "kerala",
            "hyderabadi",
            "awadhi",
            "biryani",
            "pizza",
            "sushi",
            "kebab",
        ]
        for cuisine in cuisines:
            if cuisine in notes_lower:
                return cuisine

    # Priority 2 — dietary restriction
    if "Veg" in dietary_tags or "Jain" in dietary_tags:
        return "vegetarian"

    # Priority 3 — alcohol preference
    if alcohol_preference == "yes":
        return "bar"
    elif alcohol_preference == "no":
        return "family"

    # Priority 4 — empty, let Dineout return all nearby
    return "restaurant"


def _parse_address_id(response: dict, preferred_city: str = "") -> str:
    """
    Parse real Swiggy MCP get_addresses response.

    Real response format:
    {"result": {"content": [{"type": "text", "text": "Found 11 saved addresses...\n1. [Home] Name: Address (ID: abc123)\n..."}]}}

    Tries to find address matching preferred_city first,
    falls back to Home label, then first address.
    """
    import re

    try:
        # Extract text content from MCP response
        content = response.get("result", {}).get("content", [])
        text = next((c["text"] for c in content if c.get("type") == "text"), "")

        if not text:
            return DEFAULT_MOCK_ADDRESS_ID

        # Parse lines like: "1. [Home] Name: Address (ID: abc123)"
        # or: "1. [Label] Name: address text (ID: xyz)"
        id_pattern = re.compile(r"\(ID:\s*([^\)]+)\)")
        city_pattern = (
            re.compile(preferred_city, re.IGNORECASE) if preferred_city else None
        )

        lines = text.split("\n")

        # Try to find address matching preferred city
        if city_pattern:
            for line in lines:
                if city_pattern.search(line):
                    match = id_pattern.search(line)
                    if match:
                        return match.group(1).strip()

        # Fall back to Home label
        for line in lines:
            if "[Home]" in line or "[home]" in line:
                match = id_pattern.search(line)
                if match:
                    return match.group(1).strip()

        # Fall back to first address with an ID
        for line in lines:
            match = id_pattern.search(line)
            if match:
                return match.group(1).strip()

    except Exception as e:
        import logging

        logging.warning(f"Failed to parse address response: {e}")

    return DEFAULT_MOCK_ADDRESS_ID


def _parse_location(response: dict) -> dict | None:
    """
    Parse real Swiggy MCP get_saved_locations response for Dineout.
    Returns {lat, lng} or None if parsing fails.
    """
    import re

    try:
        content = response.get("result", {}).get("content", [])
        text = next((c["text"] for c in content if c.get("type") == "text"), "")

        # Look for lat/lng patterns in text
        lat_match = re.search(r"lat[itude]*[:\s]+([0-9.]+)", text, re.IGNORECASE)
        lng_match = re.search(r"l[ong]+[itude]*[:\s]+([0-9.]+)", text, re.IGNORECASE)

        if lat_match and lng_match:
            return {
                "lat": float(lat_match.group(1)),
                "lng": float(lng_match.group(1)),
            }
    except Exception:
        pass
    return None
