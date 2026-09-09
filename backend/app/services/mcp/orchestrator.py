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
import logging
import re
from typing import Any
from app.services.mcp.food import FoodMCPClient
from app.services.mcp.instamart import InstamartMCPClient
from app.services.mcp.dineout import DineoutMCPClient
from app.services.mcp.parse_mcp import parse_restaurant_list

logger = logging.getLogger(__name__)


DEFAULT_MOCK_ADDRESS_ID = "addr_001"
DEFAULT_MOCK_LOCATION = {"lat": 26.8467, "lng": 80.9462}

# Indian cities that go by more than one name — any name maps to the whole
# group, so "Gurugram" typed by the user matches a "Gurgaon" saved address.
_CITY_SYNONYM_GROUPS = [
    {"gurugram", "gurgaon"},
    {"bengaluru", "bangalore"},
    {"mumbai", "bombay"},
    {"kolkata", "calcutta"},
    {"chennai", "madras"},
    {"puducherry", "pondicherry", "pondy"},
    {"prayagraj", "allahabad"},
    {"vadodara", "baroda"},
    {"kochi", "cochin", "ernakulam"},
    {"mysuru", "mysore"},
    {"thiruvananthapuram", "trivandrum"},
    {"varanasi", "banaras", "benares", "kashi"},
    {"kozhikode", "calicut"},
    {"kollam", "quilon"},
    {"thrissur", "trichur"},
    {"alappuzha", "alleppey"},
    {"kannur", "cannanore"},
    {"palakkad", "palghat"},
    {"belagavi", "belgaum"},
    {"kalaburagi", "gulbarga"},
    {"hubballi", "hubli"},
    {"tumakuru", "tumkur"},
    {"shivamogga", "shimoga"},
    {"ballari", "bellary"},
    {"vijayapura", "bijapur"},
    {"visakhapatnam", "vizag", "waltair"},
    {"vijayawada", "bezawada"},
    {"tiruchirappalli", "trichy", "tiruchi"},
    {"thoothukudi", "tuticorin"},
    {"tirunelveli", "nellai"},
    {"panaji", "panjim"},
    {"shimla", "simla"},
    {"kanpur", "cawnpore"},
    {"guwahati", "gauhati"},
    {"kozhikode", "calicut"},
    {"rajahmundry", "rajamahendravaram"},
]
_CITY_SYNONYMS: dict[str, set[str]] = {}
for _group in _CITY_SYNONYM_GROUPS:
    for _name in _group:
        _CITY_SYNONYMS.setdefault(_name, set()).update(_group)

# Well-known neighbourhoods → their city, for the big metros. Lets someone
# type "Koramangala" or "Bandra" and still match their Bengaluru / Mumbai
# saved address. Best-effort, not exhaustive — a real geocoder is the
# proper fix (see TODO.md §1).
_AREA_TO_CITY = {
    # Bengaluru
    "koramangala": "bengaluru", "indiranagar": "bengaluru",
    "whitefield": "bengaluru", "jayanagar": "bengaluru", "hsr": "bengaluru",
    "marathahalli": "bengaluru", "yelahanka": "bengaluru",
    "electronic city": "bengaluru", "malleshwaram": "bengaluru",
    "hebbal": "bengaluru", "btm": "bengaluru", "bellandur": "bengaluru",
    "sarjapur": "bengaluru", "banashankari": "bengaluru",
    # Mumbai
    "bandra": "mumbai", "andheri": "mumbai", "powai": "mumbai",
    "juhu": "mumbai", "colaba": "mumbai", "dadar": "mumbai",
    "worli": "mumbai", "malad": "mumbai", "goregaon": "mumbai",
    "borivali": "mumbai", "thane": "mumbai", "vashi": "mumbai",
    "chembur": "mumbai", "lower parel": "mumbai",
    # Delhi
    "hauz khas": "delhi", "saket": "delhi", "connaught place": "delhi",
    "rajouri": "delhi", "dwarka": "delhi", "rohini": "delhi",
    "lajpat nagar": "delhi", "vasant kunj": "delhi", "karol bagh": "delhi",
    "nehru place": "delhi", "janakpuri": "delhi", "greater kailash": "delhi",
    # Gurugram
    "cyber city": "gurugram", "cyber hub": "gurugram", "dlf": "gurugram",
    "golf course road": "gurugram", "sohna road": "gurugram",
    "udyog vihar": "gurugram", "mg road gurgaon": "gurugram",
    # Hyderabad
    "gachibowli": "hyderabad", "hitec city": "hyderabad",
    "hitech city": "hyderabad", "banjara hills": "hyderabad",
    "jubilee hills": "hyderabad", "madhapur": "hyderabad",
    "kondapur": "hyderabad", "secunderabad": "hyderabad",
    "kukatpally": "hyderabad", "begumpet": "hyderabad",
    # Pune
    "koregaon park": "pune", "kalyani nagar": "pune", "hinjewadi": "pune",
    "viman nagar": "pune", "baner": "pune", "kothrud": "pune",
    "hadapsar": "pune", "aundh": "pune", "wakad": "pune",
    # Chennai
    "adyar": "chennai", "velachery": "chennai", "nungambakkam": "chennai",
    "anna nagar": "chennai", "omr": "chennai", "t nagar": "chennai",
    "mylapore": "chennai", "guindy": "chennai", "porur": "chennai",
    # Kolkata
    "salt lake": "kolkata", "park street": "kolkata", "ballygunge": "kolkata",
    "new town": "kolkata", "howrah": "kolkata", "behala": "kolkata",
    # Lucknow
    "hazratganj": "lucknow", "gomti nagar": "lucknow", "aminabad": "lucknow",
    "indira nagar lucknow": "lucknow", "aliganj": "lucknow",
    "lda colony": "lucknow", "alambagh": "lucknow",
    # NCR (distinct cities but people conflate)
    "noida": "noida", "greater noida": "noida", "ghaziabad": "ghaziabad",
    "faridabad": "faridabad",
}

# Generic address-structure words — never enough to identify a city.
_ADDRESS_NOISE = {
    "flat", "floor", "block", "sector", "phase", "plot", "house", "near",
    "opposite", "opp", "behind", "beside", "next", "road", "street", "lane",
    "cross", "main", "gate", "circle", "market", "the", "and", "for",
    "home", "work", "other", "office", "tower", "wing", "apartment",
}


def _tokens(text: str) -> set[str]:
    """Lowercase words (3+ letters) from a location or address string."""
    return {w for w in re.findall(r"[a-z]+", text.lower()) if len(w) >= 3}


def _city_terms_from_text(text: str) -> set[str]:
    """
    Every city name a free-text location/address string implies — its own
    words plus renamed-city synonyms plus neighbourhood → city mappings.

      "Sector 29, Gurugram"     → {"gurugram", "gurgaon"}
      "5th Block, Koramangala"  → {"koramangala", "bengaluru", "bangalore"}
      "Jubilee Hills, Hyderabad" → {"jubilee", "hills", "hyderabad"}
    """
    if not text:
        return set()
    low = text.lower()
    out: set[str] = set()

    for tok in _tokens(text) - _ADDRESS_NOISE:
        out.add(tok)
        out |= _CITY_SYNONYMS.get(tok, set())
        mapped = _AREA_TO_CITY.get(tok)
        if mapped:
            out.add(mapped)
            out |= _CITY_SYNONYMS.get(mapped, set())

    # multi-word phrases ("cyber city", "jubilee hills") — substring match
    for area, city in _AREA_TO_CITY.items():
        if " " in area and area in low:
            out.add(city)
            out |= _CITY_SYNONYMS.get(city, set())

    return out


def _location_terms(location: str) -> set[str]:
    """
    City names the location the user typed implies. Prefers the part after
    the last comma (usually the city); falls back to the whole string.
    """
    if not location:
        return set()
    tail = location.rsplit(",", 1)[-1]
    return _city_terms_from_text(tail) or _city_terms_from_text(location)


def _line_matches_location(address_line: str, location_terms: set[str]) -> bool:
    """
    True if a saved-address line resolves to a city the request also names.
    Both sides go through _city_terms_from_text, so "typed Bengaluru" matches
    an address that only says "Koramangala".
    """
    if not location_terms:
        return False
    return bool(location_terms & _city_terms_from_text(address_line))


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
        self, location: str = "", access_token: str | None = None
    ) -> tuple[str, dict]:
        """
        Resolve the Swiggy addressId to search from.

        The user's typed `location` picks which of their saved Swiggy
        addresses to use (they may have several, in different cities). If
        none match — e.g. they typed "Gurugram" but only have a Lucknow
        address saved — we fall back to their Home / first address and set
        `city_matched=False` so the plan can say so.

        Real Swiggy MCP returns text responses that need parsing.
        No token → hardcoded mock defaults.
        """
        if not access_token:
            return DEFAULT_MOCK_ADDRESS_ID, {**DEFAULT_MOCK_LOCATION, "city_matched": True}

        addresses_result, locations_result = await asyncio.gather(
            self.food.get_addresses(access_token=access_token),
            self.dineout.get_saved_locations(access_token=access_token),
            return_exceptions=True,
        )

        terms = _location_terms(location)

        address_id, food_matched, food_label = DEFAULT_MOCK_ADDRESS_ID, False, ""
        if not isinstance(addresses_result, Exception):
            address_id, food_matched, food_label = _resolve_address_id(
                addresses_result, terms
            )

        # Dineout also uses an addressId (same text format as get_addresses).
        dineout_address_id, dineout_matched, dineout_label = (
            DEFAULT_MOCK_ADDRESS_ID,
            False,
            "",
        )
        if not isinstance(locations_result, Exception):
            dineout_address_id, dineout_matched, dineout_label = _resolve_address_id(
                locations_result, terms
            )

        city_matched = not terms or food_matched or dineout_matched
        address_label = food_label or dineout_label
        logger.info(
            "Resolved addressId food=%s dineout=%s (requested=%r, city match=%s, using=%r)",
            address_id,
            dineout_address_id,
            location,
            city_matched,
            address_label,
        )
        return address_id, {
            "address_id": dineout_address_id,
            "lat": DEFAULT_MOCK_LOCATION["lat"],
            "lng": DEFAULT_MOCK_LOCATION["lng"],
            "city_matched": city_matched,
            "address_label": address_label,
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
        refine: str | None = None,
        search_offset: int = 0,
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
            refine:             picker free-text — overrides the food/dineout
                                search query when set
            search_offset:      pagination offset for the "show more" button

        Returns:
            {food, instamart, dineout, venue_mode, budget_split, coordinates}
        """
        refine = (refine or "").strip() or None
        budget_split = self._calculate_budget_split(budget, venue_mode)
        address_id, saved_location = await self.resolve_addresses(
            location=location, access_token=access_token
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
            food_query = refine or _food_query(
                event_type, dietary_tags, notes, venue_mode
            )
            tasks.append(
                (
                    "food",
                    self.food.search_restaurants(
                        address_id=address_id,
                        query=food_query,
                        dietary_filters=dietary_tags,
                        budget_per_head=budget_split["food"] // max(guest_count, 1),
                        health_focus=health_focus,
                        offset=search_offset,
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
            # Dineout's real API wants a single word — take the last word of
            # a multi-word refine (usually the cuisine: "cosy italian" → italian).
            dineout_query = (
                refine.split()[-1]
                if refine
                else _dineout_query(event_type, dietary_tags, alcohol_preference, notes)
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
                        offset=search_offset,
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
        context["refine"] = refine
        context["search_offset"] = search_offset

        used_gps = bool(lat and lng)

        # The real saved address the search actually ran against (only known
        # when authenticated, and not when GPS overrode it). Surfaced in the
        # picker + plan so the user can see it at a glance.
        address_label = saved_location.get("address_label")
        if access_token and not used_gps and address_label:
            context["address_used"] = address_label

        # If the user typed a city but has no Swiggy address there (and gave no
        # GPS), every search ran against their default address — tell them.
        if (
            access_token
            and location
            and not used_gps
            and not saved_location.get("city_matched", True)
        ):
            context["location_warning"] = (
                f'No saved Swiggy address matches "{location}" — showing results '
                f"for your default saved address. Add a {location} address in the "
                "Swiggy app for local results."
            )
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
            elif service_name in ("food", "dineout"):
                # Real Swiggy MCP returns a text envelope; parse it into the
                # same {"data": {"restaurants": [...]}} shape the mock emits.
                # None → not parseable text (mock / error) → keep as-is.
                context[service_name] = parse_restaurant_list(result) or result
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


def _address_label(line: str) -> str:
    """
    A short human label for a saved-address line, for showing the user which
    address a plan was built from.

      "2. [Home] Uttkarsh Mishra: E-1/432, LDA Colony, Lucknow (ID: 43530781)"
      → "[Home] E-1/432, LDA Colony, Lucknow"
    """
    text = re.sub(r"\s*\(ID:[^)]*\)\s*$", "", line).strip()
    text = re.sub(r"^\s*\d+[.)]\s*", "", text)  # drop leading "2." / "2)"
    label = re.match(r"(\[[^\]]+\]\s*)?", text).group(0)
    rest = text[len(label):]
    if ":" in rest:  # "Name: address" → keep the address part
        rest = rest.split(":", 1)[1].strip()
    return f"{label}{rest}".strip()[:120]


def _resolve_address_id(
    response: dict, location_terms: set[str] | None = None
) -> tuple[str, bool, str]:
    """
    Parse a real Swiggy get_addresses / get_saved_locations response and pick
    one addressId.

    Real response format:
    {"result": {"content": [{"type": "text", "text":
      "Found 11 saved addresses...\n1. [Home] Name: Address, City (ID: abc123)\n..."}]}}

    Order of preference:
      1. a line sharing a city word with `location_terms` (from _location_terms)
      2. the [Home] address
      3. the first address with an ID

    Returns (address_id, matched_requested_city, human_label).
    """
    terms = location_terms or set()
    try:
        content = response.get("result", {}).get("content", [])
        text = next((c["text"] for c in content if c.get("type") == "text"), "")
        if not text:
            return DEFAULT_MOCK_ADDRESS_ID, False, ""

        id_pattern = re.compile(r"\(ID:\s*([^)]+)\)")
        lines = text.split("\n")

        if terms:
            for line in lines:
                if _line_matches_location(line, terms):
                    match = id_pattern.search(line)
                    if match:
                        return match.group(1).strip(), True, _address_label(line)

        for line in lines:
            if "[home]" in line.lower():
                match = id_pattern.search(line)
                if match:
                    return match.group(1).strip(), False, _address_label(line)

        for line in lines:
            match = id_pattern.search(line)
            if match:
                return match.group(1).strip(), False, _address_label(line)

    except Exception as e:
        logger.warning("Failed to parse address response: %s", e)

    return DEFAULT_MOCK_ADDRESS_ID, False, ""


def _parse_address_id(response: dict, preferred_city: str = "") -> str:
    """Back-compat shim — the addressId only, dropping the match flag + label."""
    return _resolve_address_id(response, _location_terms(preferred_city))[0]
