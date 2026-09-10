"""
api/v1/endpoints/search.py — Restaurant discovery before plan generation.

CONCEPT: Two-step plan generation
-----------------------------------
Old flow (single step):
  User fills form → Generate plan → Hope Claude picks good restaurants

New flow (two steps):
  Step 1: User fills form
  Step 2: POST /plans/search → MCP fetches real restaurants → user picks
  Step 3: POST /plans/generate with selected IDs → focused plan

WHY TWO STEPS?
  - User control: they see real options and choose
  - Better plans: Claude writes about a specific chosen restaurant
  - Better offers: we search offers for the exact chosen restaurant
  - Trust: user sees real data before committing to a plan

This endpoint is fast (~300ms) — no Claude call, just MCP data.
It returns structured restaurant cards ready to render in the UI.
"""

import asyncio

from fastapi import APIRouter, Depends, Header, HTTPException
from app.core.ratelimit import rate_limit
from app.schemas.plan import SearchRequest
from app.services.mcp.orchestrator import MCPOrchestrator
from app.services.mcp.parse_mcp import (
    mcp_text,
    parse_restaurant_details,
    parse_restaurant_list,
)

router = APIRouter()

# Cheap (no Claude), but "refine" + "show more" can spam it.
_SEARCH_LIMIT = Depends(rate_limit("search", limit=90, window_seconds=3600))
_orchestrator = None

# How many options to show in the picker per service.
_MAX_OPTIONS = 8


def get_orchestrator() -> MCPOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = MCPOrchestrator()
    return _orchestrator


def _rank_key(r: dict, refine_terms: set[str] | None = None):
    """
    Sort restaurants best-first: refine-text matches, then highest rating,
    then the MCP's own relevance order, then nearest.
    """
    hay = " ".join(
        str(r.get(k, "")).lower()
        for k in ("name", "cuisine", "locality")
    )
    matches = -sum(1 for t in (refine_terms or set()) if t in hay)
    rating = r.get("rating") or 0
    original = r.get("_rank", 999)
    distance = r.get("distance_km") or r.get("distanceKm") or 999
    return (matches, -float(rating), original, float(distance))


@router.post(
    "/",
    summary="Discover restaurant options before plan generation",
    dependencies=[_SEARCH_LIMIT],
)
async def search_restaurants(
    request: SearchRequest,
    x_session_id: str | None = Header(None, alias="X-Session-ID"),
):
    """
    Fetch restaurant options from Swiggy MCP servers.

    Called after user fills the event form but BEFORE plan generation.
    Returns structured restaurant cards the user picks from.

    No Claude call — pure MCP data, fast response (~300ms).

    Returns:
        {
          "dineout": [{id, name, rating, cost_for_two, distance, known_for, slots, offers}],
          "food": [{id, name, rating, delivery_time, price_for_two, top_dishes, offers}],
          "venue_mode": "hybrid"
        }
    """
    access_token = None
    if x_session_id:
        from app.api.v1.endpoints.auth import get_access_token

        access_token = await get_access_token(x_session_id)
    orchestrator = get_orchestrator()

    context = await orchestrator.gather_context(
        location=request.location,
        event_type=request.event_type,
        venue_mode=request.venue_mode,
        dietary_tags=request.dietary_tags,
        guest_count=request.guest_count,
        budget=request.budget,
        start_hour=request.start_hour,
        health_focus=request.health_focus,
        lat=request.lat,
        lng=request.lng,
        notes=request.notes,
        alcohol_preference=request.alcohol_preference,
        access_token=access_token,
        refine=request.refine,
        search_offset=request.offset,
    )

    refine_terms = {
        w for w in (request.refine or "").lower().split() if len(w) > 2
    }

    def rank(rows: list[dict]) -> list[dict]:
        rows.sort(key=lambda r: _rank_key(r, refine_terms))
        return [
            {k: v for k, v in r.items() if k != "_rank"}
            for r in rows[:_MAX_OPTIONS]
        ]

    # Extract and format restaurant options for the picker UI
    dineout_options = []
    food_options = []

    # Dineout restaurants — best-rated first, capped at _MAX_OPTIONS
    if context.get("dineout") and "error" not in context["dineout"]:
        raw = context["dineout"]
        restaurants = raw.get("data", {}).get("restaurants", raw.get("restaurants", []))
        for r in restaurants:
            dineout_options.append(
                {
                    "id": r.get("id"),
                    "name": r.get("name"),
                    "cuisine": r.get("cuisine"),
                    "rating": r.get("rating"),
                    "cost_for_two": r.get("costForTwo") or r.get("cost_for_two"),
                    "distance_km": r.get("distanceKm") or r.get("distance_km"),
                    "locality": r.get("locality"),
                    "ambience": r.get("ambience", []),
                    "known_for": r.get("knownFor") or r.get("known_for", []),
                    "available_slots": r.get("availableSlots")
                    or r.get("available_slots", []),
                    "offers": r.get("offers", []),
                    "availability": r.get("availability", "AVAILABLE"),
                    "_rank": r.get("_rank", 999),
                }
            )
        dineout_options = rank(dineout_options)

    # Food restaurants
    if context.get("food") and "error" not in context["food"]:
        raw = context["food"]
        restaurants = raw.get("data", {}).get("restaurants", raw.get("restaurants", []))
        for r in restaurants:
            food_options.append(
                {
                    "id": r.get("id"),
                    "name": r.get("name"),
                    "cuisine": r.get("cuisine"),
                    "rating": r.get("rating"),
                    "delivery_time_mins": r.get("deliveryTimeMinutes")
                    or r.get("delivery_time_mins"),
                    "delivery_time_range": r.get("deliveryTimeRange"),
                    "price_for_two": r.get("priceForTwo") or r.get("price_for_two"),
                    "distance_km": r.get("distanceKm") or r.get("distance_km"),
                    "locality": r.get("locality"),
                    "veg": r.get("veg"),
                    "image_url": r.get("imageUrl"),
                    "sponsored": r.get("sponsored", False),
                    "top_dishes": r.get("topDishes") or r.get("top_dishes", []),
                    "offers": r.get("offers", []),
                    "availability_status": r.get("availabilityStatus", "OPEN"),
                    "_rank": r.get("_rank", 999),
                }
            )
        food_options = rank(food_options)

    def _data(svc: str) -> dict:
        ctx = context.get(svc) or {}
        return ctx.get("data", {}) if isinstance(ctx, dict) else {}

    dineout_data = _data("dineout")

    return {
        "dineout": dineout_options,
        "food": food_options,
        "venue_mode": request.venue_mode,
        "budget_split": context.get("budget_split", {}),
        "refine": request.refine,
        "offset": request.offset,
        # More pages available from Swiggy for the "show more options" button.
        "dineout_has_more": bool(dineout_data.get("hasMore")),
        "food_has_more": bool(_data("food").get("hasMore")),
        # Set when the typed city has no matching saved Swiggy address —
        # the picker shows results for the user's default address instead.
        "location_warning": context.get("location_warning"),
        # The saved Swiggy address the search actually ran against
        # (only when authenticated). null in demo/mock mode.
        "address_used": context.get("address_used"),
        # Search coords Swiggy Dineout reported — needed for get_restaurant_details.
        "dineout_coordinates": dineout_data.get("coordinates"),
    }


@router.get("/restaurant/{restaurant_id}", summary="Full details for one Dineout restaurant")
async def restaurant_details(
    restaurant_id: str,
    lat: float | None = None,
    lng: float | None = None,
    x_session_id: str | None = Header(None, alias="X-Session-ID"),
):
    """
    get_restaurant_details for one Dineout restaurant — cuisine, cost,
    amenities, timings, offers. Used by the picker to expand a card the
    user is considering. Returns {} in demo/mock mode.
    """
    access_token = None
    if x_session_id:
        from app.api.v1.endpoints.auth import get_access_token

        access_token = await get_access_token(x_session_id)
    if not access_token:
        return {}
    try:
        raw = await get_orchestrator().dineout.get_restaurant_details(
            restaurant_id, lat=lat, lng=lng, access_token=access_token
        )
    except Exception:  # noqa: BLE001
        return {}
    return parse_restaurant_details(raw) or {}


@router.get("/_debug", summary="Raw Swiggy MCP text responses (needs a session)")
async def search_debug(
    x_session_id: str | None = Header(None, alias="X-Session-ID"),
):
    """
    Returns the raw, unparsed text Swiggy MCP sends back for a Food and a
    Dineout search — used to tune `parse_mcp.py` against live output.
    Requires a valid Swiggy session; useless (and returns 400) without one.
    """
    access_token = None
    if x_session_id:
        from app.api.v1.endpoints.auth import get_access_token

        access_token = await get_access_token(x_session_id)
    if not access_token:
        raise HTTPException(
            status_code=400, detail="Connect Swiggy first — this needs a live token."
        )

    orch = get_orchestrator()
    address_id, saved = await orch.resolve_addresses(
        location="", access_token=access_token
    )
    dineout_address_id = saved.get("address_id", address_id)

    food_raw, dineout_raw = await asyncio.gather(
        orch.food.search_restaurants(
            address_id=address_id, query="restaurant", access_token=access_token
        ),
        orch.dineout.search_restaurants(
            lat=0,
            lng=0,
            address_id=dineout_address_id,
            query="restaurant",
            access_token=access_token,
        ),
        return_exceptions=True,
    )

    # Also grab get_restaurant_details for the first dineout hit — the list is
    # sparse (name + rating + locality only); we need this format to enrich it.
    details_raw = None
    d_parsed = _try_parse(dineout_raw)
    if d_parsed:
        first = d_parsed[0]
        coords = (parse_restaurant_list(dineout_raw) or {}).get("data", {}).get(
            "coordinates", {}
        )
        try:
            details_raw = await orch.dineout.get_restaurant_details(
                first["id"],
                lat=coords.get("lat"),
                lng=coords.get("lng"),
                access_token=access_token,
            )
        except Exception as e:  # noqa: BLE001 — debug endpoint
            details_raw = e

    return {
        "address_id": address_id,
        "food_text": _safe_text(food_raw),
        "dineout_text": _safe_text(dineout_raw),
        "food_parsed": _try_parse(food_raw),
        "dineout_parsed": d_parsed,
        "restaurant_details_text": _safe_text(details_raw) if details_raw else None,
    }


def _safe_text(result) -> str:
    if isinstance(result, Exception):
        return f"<error: {result}>"
    return mcp_text(result) or f"<not text: {str(result)[:400]}>"


def _try_parse(result):
    if isinstance(result, Exception):
        return None
    parsed = parse_restaurant_list(result)
    return parsed["data"]["restaurants"] if parsed else None
