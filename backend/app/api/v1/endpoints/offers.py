"""
api/v1/endpoints/offers.py — Live Swiggy offers.

The planner already fetches offers internally (OffersEngine) and folds them
into the plan. This endpoint exposes the same data on its own so the
frontend can show "deals near you" before a plan is generated, and so the
offer set can be inspected / debugged in isolation.

  GET /api/v1/offers/?location=Lucknow&budget=3000
"""

from fastapi import APIRouter, Query

from app.services.offers.engine import OffersEngine

router = APIRouter()

_engine = OffersEngine()


@router.get("/", summary="Active Swiggy offers for a location + budget")
async def list_offers(
    location: str = Query(..., min_length=2, description="City or area name"),
    budget: int = Query(2000, ge=100, le=50000, description="Event budget in INR"),
):
    """
    Return the Swiggy offers currently active for this location and budget.

    Offers whose minimum order value exceeds the budget are filtered out.
    Results are Redis-cached for OFFERS_CACHE_TTL (5 min) — see OffersEngine.
    """
    offers = await _engine.get_active_offers(location=location, budget=budget)
    return {
        "location": location,
        "budget": budget,
        "count": len(offers),
        "offers": offers,
    }
