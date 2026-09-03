"""
tests/unit/test_offers.py — Unit tests for the offer engine.

WHAT WE TEST:
  - Offers are filtered by minimum order value
  - All offer types are returned when budget is sufficient
  - Empty list returned when budget is too low for all offers
  - parse_plan.py extracts cost strings correctly
  - parse_plan.py extracts timeline steps correctly
  - parse_plan.py handles missing sections gracefully
"""

import pytest
from app.services.offers.engine import OffersEngine
from app.lib.parse_plan import parse_plan_text, get_section, parse_timeline


class TestOffersEngine:
    """Tests for OffersEngine mock offer filtering."""

    def test_filters_by_min_order(self):
        """
        Offers with min_order above budget should be excluded.
        A ₹300 budget should not include offers requiring ₹499+.
        """
        engine = OffersEngine()
        offers = engine._mock_offers(location="Lucknow", budget=300)
        for offer in offers:
            assert offer["min_order"] <= 300, (
                f"Offer {offer['code']} has min_order {offer['min_order']} "
                f"but budget is 300"
            )

    def test_returns_all_offers_for_large_budget(self):
        """A large budget should unlock all available offers."""
        engine = OffersEngine()
        offers = engine._mock_offers(location="Lucknow", budget=10000)
        assert len(offers) > 0

    def test_returns_empty_for_tiny_budget(self):
        """Budget below all min_orders should return empty list."""
        engine = OffersEngine()
        offers = engine._mock_offers(location="Lucknow", budget=50)
        assert offers == []

    def test_offer_structure_has_required_fields(self):
        """Every offer must have the fields the frontend and AI expect."""
        engine = OffersEngine()
        offers = engine._mock_offers(location="Lucknow", budget=5000)
        required_fields = ["service", "type", "code", "description", "min_order", "max_saving"]
        for offer in offers:
            for field in required_fields:
                assert field in offer, f"Offer missing field: {field}"

    def test_offer_services_are_valid(self):
        """Service field must be one of the three Swiggy services."""
        engine = OffersEngine()
        offers = engine._mock_offers(location="Lucknow", budget=5000)
        valid_services = {"food", "instamart", "dineout"}
        for offer in offers:
            assert offer["service"] in valid_services, (
                f"Invalid service: {offer['service']}"
            )


class TestParsePlanText:
    """
    Tests for the server-side plan parser.
    Ensures plan text is correctly parsed into structured DB fields.
    """

    SAMPLE_PLAN = """[BRIEF]
A romantic hybrid date starting at Farzi Cafe's rooftop.

[TIMELINE]
8:00 PM | 🍽 | Arrive at Farzi Cafe | Head to the rooftop
9:30 PM | 🏠 | Head home | Your delivery arrives shortly
10:00 PM | 🕯 | Set ambience | Light the candles

[DINEOUT]
RESTAURANT: Farzi Cafe
WHY: Perfect rooftop ambience
SLOT: 8:00 PM
OFFER: 15% off pre-booking
COST: ₹1,275

[FOOD]
RESTAURANT: Meghana Foods
DISHES: Biryani (₹220), Pepper Chicken (₹280)
OFFER: 50% off up to ₹100
COST: ₹400

[INSTAMART]
Ambience:
• Tealight Candles (Hosley) — ₹149 x 1
ESTIMATED TOTAL: ₹149

[HEALTH]
This plan balances indulgence with lighter options.

[OFFERS]
• Dineout: 15% off — saves ₹225
• Food: 50% off — saves ₹100
TOTAL SAVINGS: ₹325

[COST]
Dineout: ₹1,275 | Food Delivery: ₹400 | Instamart: ₹149
TOTAL: ₹1,824"""

    def test_extracts_brief(self):
        result = parse_plan_text(self.SAMPLE_PLAN)
        assert "romantic hybrid date" in result["brief"]
        assert "Farzi Cafe" in result["brief"]

    def test_extracts_timeline(self):
        result = parse_plan_text(self.SAMPLE_PLAN)
        assert len(result["timeline"]) == 3
        assert result["timeline"][0]["time"] == "8:00 PM"
        assert result["timeline"][0]["emoji"] == "🍽"
        assert result["timeline"][0]["title"] == "Arrive at Farzi Cafe"

    def test_extracts_dineout(self):
        result = parse_plan_text(self.SAMPLE_PLAN)
        assert "Farzi Cafe" in result["dineout"]
        assert "₹1,275" in result["dineout"]

    def test_extracts_food(self):
        result = parse_plan_text(self.SAMPLE_PLAN)
        assert "Meghana Foods" in result["food"]
        assert "Biryani" in result["food"]

    def test_extracts_instamart(self):
        result = parse_plan_text(self.SAMPLE_PLAN)
        assert "Tealight Candles" in result["instamart"]
        assert "₹149" in result["instamart"]

    def test_extracts_total_cost(self):
        result = parse_plan_text(self.SAMPLE_PLAN)
        assert result["totalCost"] == "₹1,824"

    def test_extracts_total_savings(self):
        result = parse_plan_text(self.SAMPLE_PLAN)
        assert result["totalSavings"] == "₹325"

    def test_extracts_per_service_costs(self):
        """Per-service costs are parsed from the [COST] line (point #8)."""
        result = parse_plan_text(self.SAMPLE_PLAN)
        assert result["dineoutCost"] == "₹1,275"
        assert result["foodCost"] == "₹400"  # "Food Delivery: ₹400"
        assert result["instamartCost"] == "₹149"

    def test_absent_service_cost_is_blank(self):
        """A stay-in plan has no Dineout line → dineoutCost is ''."""
        plan = "[COST]\nFood Delivery: ₹700 | Instamart: ₹300\nTOTAL: ₹1,000"
        result = parse_plan_text(plan)
        assert result["dineoutCost"] == ""
        assert result["foodCost"] == "₹700"
        assert result["instamartCost"] == "₹300"

    def test_handles_missing_section(self):
        """
        If a section is absent (e.g. no Dineout for home-mode events),
        the parser must return empty string, not crash.
        """
        minimal_plan = "[BRIEF]\nA simple home evening.\n\n[COST]\nTOTAL: ₹500"
        result = parse_plan_text(minimal_plan)
        assert result["brief"] == "A simple home evening."
        assert result["dineout"] == ""
        assert result["food"] == ""
        assert result["totalCost"] == "₹500"

    def test_decodes_enqueue_symbols(self):
        """
        Plans from the SSE stream have ⏎ instead of newlines.
        Parser must decode these before extracting sections.
        """
        encoded_plan = "[BRIEF]⏎A romantic evening.⏎⏎[COST]⏎TOTAL: ₹1,000"
        result = parse_plan_text(encoded_plan)
        assert result["brief"] == "A romantic evening."
        assert result["totalCost"] == "₹1,000"

    def test_timeline_skips_non_pipe_lines(self):
        """Lines without | in the timeline section should be ignored."""
        raw = "8:00 PM | 🍽 | Title | Detail\nThis is a header line\n9:00 PM | 🏠 | Home | Back"
        steps = parse_timeline(raw)
        assert len(steps) == 2
        assert steps[0]["title"] == "Title"
        assert steps[1]["title"] == "Home"