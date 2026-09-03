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

from fastapi import APIRouter, Header
from app.schemas.plan import SearchRequest
from app.services.mcp.orchestrator import MCPOrchestrator

router = APIRouter()
_orchestrator = None


def get_orchestrator() -> MCPOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = MCPOrchestrator()
    return _orchestrator


@router.post("/", summary="Discover restaurant options before plan generation")
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
    )

    # Extract and format restaurant options for the picker UI
    dineout_options = []
    food_options = []

    # Dineout restaurants
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
                    "ambience": r.get("ambience", []),
                    "known_for": r.get("knownFor") or r.get("known_for", []),
                    "available_slots": r.get("availableSlots")
                    or r.get("available_slots", []),
                    "offers": r.get("offers", []),
                    "availability": r.get("availability", "AVAILABLE"),
                }
            )

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
                    "price_for_two": r.get("priceForTwo") or r.get("price_for_two"),
                    "distance_km": r.get("distanceKm") or r.get("distance_km"),
                    "top_dishes": r.get("topDishes") or r.get("top_dishes", []),
                    "offers": r.get("offers", []),
                    "availability_status": r.get("availabilityStatus", "OPEN"),
                }
            )

    return {
        "dineout": dineout_options,
        "food": food_options,
        "venue_mode": request.venue_mode,
        "budget_split": context.get("budget_split", {}),
        # Set when the typed city has no matching saved Swiggy address —
        # the picker shows results for the user's default address instead.
        "location_warning": context.get("location_warning"),
    }
