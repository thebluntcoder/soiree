"""
tests/unit/test_planner.py — Unit tests for the AI planner prompt builders.

We test prompts.py separately from planner.py because:
  - Prompt quality directly determines plan quality
  - Prompts are pure functions — no async, no network, easy to test
  - If prompts break, everything downstream breaks

WHAT WE TEST:
  - System prompt contains required section markers
  - System prompt contains the grounding rule (no hallucination)
  - User prompt injects event data correctly
  - User prompt injects MCP context as JSON
  - User prompt handles missing MCP sections gracefully
  - Guest formatting: named guests vs headcount
  - Dietary tag merging: group + per-guest tags combined
  - Health focus label mapping: 0-30 indulgent, 70-100 healthy
"""

import json
import pytest
from app.services.ai.prompts import build_system_prompt, build_user_prompt


class TestSystemPrompt:
    """Tests for build_system_prompt() — the static Claude persona."""

    def test_contains_all_section_markers(self):
        """
        All 8 section markers must be present.
        If any is missing, Claude won't know to write that section.
        """
        prompt = build_system_prompt()
        required_markers = [
            "[BRIEF]", "[TIMELINE]", "[DINEOUT]", "[FOOD]",
            "[INSTAMART]", "[HEALTH]", "[OFFERS]", "[COST]"
        ]
        for marker in required_markers:
            assert marker in prompt, f"Missing section marker: {marker}"

    def test_contains_grounding_rule(self):
        """
        The prompt must tell Claude not to invent restaurant names.
        This is the most important safety rule — hallucinated restaurants
        destroy user trust.
        """
        prompt = build_system_prompt()
        # Check for key phrases that enforce data grounding
        assert "Never" in prompt or "never" in prompt
        assert "invent" in prompt or "MCP context" in prompt

    def test_contains_timeline_format(self):
        """Timeline format must specify the pipe-separated structure."""
        prompt = build_system_prompt()
        assert "TIME | EMOJI | TITLE | DETAIL" in prompt or "|" in prompt

    def test_contains_cost_format(self):
        """Cost section must specify INR format."""
        prompt = build_system_prompt()
        assert "TOTAL" in prompt
        assert "₹" in prompt

    def test_is_string_and_nonempty(self):
        prompt = build_system_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 500  # A meaningful system prompt is never tiny


class TestUserPrompt:
    """Tests for build_user_prompt() — the dynamic per-request prompt."""

    def test_injects_location(self, sample_event_data, sample_mcp_context, sample_offers):
        """Location must appear in the prompt so Claude knows where the event is."""
        prompt = build_user_prompt(sample_event_data, sample_mcp_context, sample_offers)
        assert "Lucknow" in prompt

    def test_injects_budget(self, sample_event_data, sample_mcp_context, sample_offers):
        """Budget must appear so Claude can reference it in the plan."""
        prompt = build_user_prompt(sample_event_data, sample_mcp_context, sample_offers)
        assert "3,000" in prompt or "3000" in prompt

    def test_injects_event_type(self, sample_event_data, sample_mcp_context, sample_offers):
        """Event type must appear — drives tone and recommendations."""
        prompt = build_user_prompt(sample_event_data, sample_mcp_context, sample_offers)
        assert "date" in prompt.lower() or "Date" in prompt

    def test_injects_food_mcp_data(self, sample_event_data, sample_mcp_context, sample_offers):
        """
        MCP food data must be serialised into the prompt.
        Claude must see real restaurant names — not generate them.
        """
        prompt = build_user_prompt(sample_event_data, sample_mcp_context, sample_offers)
        assert "Meghana Foods" in prompt

    def test_injects_dineout_mcp_data(self, sample_event_data, sample_mcp_context, sample_offers):
        """Dineout restaurant must appear in prompt."""
        prompt = build_user_prompt(sample_event_data, sample_mcp_context, sample_offers)
        assert "Farzi Cafe" in prompt

    def test_injects_instamart_mcp_data(self, sample_event_data, sample_mcp_context, sample_offers):
        """Instamart products must appear in prompt."""
        prompt = build_user_prompt(sample_event_data, sample_mcp_context, sample_offers)
        assert "Tealight Candles" in prompt

    def test_injects_offers(self, sample_event_data, sample_mcp_context, sample_offers):
        """Active offers must appear so Claude can mention savings."""
        prompt = build_user_prompt(sample_event_data, sample_mcp_context, sample_offers)
        assert "SWIGGY50" in prompt or "EARLYBIRD15" in prompt

    def test_headcount_only_guests(self, sample_mcp_context, sample_offers):
        """
        When guests list is empty, guest_count should be used.
        The prompt should mention the count not individual names.
        """
        event_data = {
            "event_type": "friends",
            "venue_mode": "home",
            "location": "Bangalore",
            "start_hour": 19,
            "budget": 2000,
            "guest_count": 5,
            "guests": [],        # headcount only
            "dietary_tags": ["Veg"],
            "health_focus": 50,
            "notes": None,
        }
        prompt = build_user_prompt(event_data, sample_mcp_context, sample_offers)
        assert "5" in prompt
        assert "Veg" in prompt

    def test_named_guests_appear(self, sample_mcp_context, sample_offers):
        """Named guests and their dietary tags must be included."""
        event_data = {
            "event_type": "birthday",
            "venue_mode": "hybrid",
            "location": "Mumbai",
            "start_hour": 20,
            "budget": 4000,
            "guest_count": 3,
            "guests": [
                {"name": "Anjali", "dietary_tags": ["Veg"]},
                {"name": "Rahul", "dietary_tags": []},
            ],
            "dietary_tags": [],
            "health_focus": 50,
            "notes": "It is Anjali's birthday",
        }
        prompt = build_user_prompt(event_data, sample_mcp_context, sample_offers)
        assert "Anjali" in prompt
        assert "Rahul" in prompt
        assert "birthday" in prompt.lower() or "Birthday" in prompt

    def test_health_focus_low_label(self, sample_mcp_context, sample_offers):
        """health_focus <= 30 should label as indulgent."""
        event_data = {
            "event_type": "date",
            "venue_mode": "out",
            "location": "Delhi",
            "start_hour": 20,
            "budget": 3000,
            "guest_count": 2,
            "guests": [],
            "dietary_tags": [],
            "health_focus": 20,   # indulgent
            "notes": None,
        }
        prompt = build_user_prompt(event_data, sample_mcp_context, sample_offers)
        assert "indulgent" in prompt.lower()

    def test_health_focus_high_label(self, sample_mcp_context, sample_offers):
        """health_focus >= 70 should label as health-conscious."""
        event_data = {
            "event_type": "date",
            "venue_mode": "out",
            "location": "Delhi",
            "start_hour": 20,
            "budget": 3000,
            "guest_count": 2,
            "guests": [],
            "dietary_tags": [],
            "health_focus": 80,   # healthy
            "notes": None,
        }
        prompt = build_user_prompt(event_data, sample_mcp_context, sample_offers)
        assert "health" in prompt.lower()

    def test_notes_appear_when_provided(self, sample_mcp_context, sample_offers):
        """Free-text notes must be included so Claude uses them."""
        event_data = {
            "event_type": "date",
            "venue_mode": "hybrid",
            "location": "Lucknow",
            "start_hour": 20,
            "budget": 3000,
            "guest_count": 2,
            "guests": [],
            "dietary_tags": [],
            "health_focus": 50,
            "notes": "It is our anniversary",
        }
        prompt = build_user_prompt(event_data, sample_mcp_context, sample_offers)
        assert "anniversary" in prompt

    def test_missing_mcp_section_handled(self, sample_event_data, sample_offers):
        """
        If a MCP service returned None (e.g. out mode — no instamart),
        the prompt should not crash and should note it's unavailable.
        """
        mcp_context = {
            "food": None,
            "instamart": None,
            "dineout": {
                "restaurants": [{"id": "d1", "name": "Test Restaurant", "available_slots": ["8:00 PM"]}]
            },
            "venue_mode": "out",
            "budget_split": {"dineout": 3000, "food": 0, "instamart": 0},
        }
        # Should not raise
        prompt = build_user_prompt(sample_event_data, mcp_context, sample_offers)
        assert isinstance(prompt, str)
        assert len(prompt) > 100

    def test_budget_split_appears(self, sample_event_data, sample_mcp_context, sample_offers):
        """Budget split per service must be in prompt so Claude respects it."""
        prompt = build_user_prompt(sample_event_data, sample_mcp_context, sample_offers)
        assert "1,800" in prompt or "1800" in prompt  # dineout split (60% of ₹3000)


class TestUserPromptSelection:
    """
    build_user_prompt also carries the restaurant the user picked in the
    Step 2 picker straight through to Claude (points #3 / #4).
    """

    def test_selected_dineout_rendered(
        self, sample_event_data, sample_mcp_context, sample_offers
    ):
        chosen = {
            "id": "dine_x",
            "name": "Mantar",
            "cuisine": "North Indian",
            "rating": 4.8,
            "cost_for_two": 2400,
            "distance_km": 3.2,
            "known_for": ["Rooftop", "Live music"],
            "available_slots": [{"time": "8:00 PM"}, {"time": "8:30 PM"}],
            "offers": [{"description": "15% off pre-booking"}],
        }
        prompt = build_user_prompt(
            sample_event_data,
            sample_mcp_context,
            sample_offers,
            selected_dineout=chosen,
        )
        assert "USER HAS CHOSEN THIS DINEOUT RESTAURANT" in prompt
        assert "Mantar" in prompt
        assert "8:00 PM" in prompt
        assert "Do NOT suggest alternatives" in prompt

    def test_selected_food_rendered(
        self, sample_event_data, sample_mcp_context, sample_offers
    ):
        chosen = {
            "id": "rest_x",
            "name": "Bakingo",
            "cuisine": "Bakery",
            "rating": 4.6,
            "top_dishes": [{"name": "Truffle Cake", "price": 649}],
            "offers": [],
        }
        prompt = build_user_prompt(
            sample_event_data,
            sample_mcp_context,
            sample_offers,
            selected_food=chosen,
        )
        assert "USER HAS CHOSEN THIS FOOD RESTAURANT" in prompt
        assert "Bakingo" in prompt

    def test_no_selection_has_no_choice_block(
        self, sample_event_data, sample_mcp_context, sample_offers
    ):
        prompt = build_user_prompt(sample_event_data, sample_mcp_context, sample_offers)
        assert "USER HAS CHOSEN" not in prompt

    def test_alcohol_preference_rendered(
        self, sample_mcp_context, sample_offers
    ):
        event = {
            "event_type": "friends",
            "venue_mode": "out",
            "location": "Lucknow",
            "start_hour": 20,
            "budget": 3000,
            "guest_count": 4,
            "guests": [],
            "dietary_tags": [],
            "health_focus": 50,
            "notes": None,
            "alcohol_preference": "no",
        }
        prompt = build_user_prompt(event, sample_mcp_context, sample_offers)
        assert "No alcohol" in prompt