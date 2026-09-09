"""
tests/unit/test_parse_mcp.py — parsing Swiggy MCP restaurant-list text.

The real Swiggy MCP returns human-readable text; parse_mcp turns it into the
same {"data": {"restaurants": [...]}} shape the mock clients emit so the
picker + planner work identically. See TODO.md §2.
"""

from app.services.mcp.parse_mcp import mcp_text, parse_restaurant_list


def _env(text: str) -> dict:
    return {"result": {"content": [{"type": "text", "text": text}]}}


DINEOUT = _env(
    'Found 39 restaurant(s) matching "restaurant", showing 10. '
    "29 more available, call again with offset=10.\n"
    "1. Farzi Cafe — | 4.6★ | | Hazratganj (ID: 824088)\n"
    "2. Barkaas Indo Arabic Restaurant — | 4.8★ | 2.3 km | Hazratganj (ID: 824099)\n"
    "3. The Great Kabab Factory — | 4.3★ | | Gomti Nagar (ID: 824111)\n"
    "Response includes lat/lng for downstream calls."
)


class TestMcpText:
    def test_pulls_text(self):
        assert mcp_text(DINEOUT).startswith("Found 39")

    def test_non_envelope_is_empty(self):
        assert mcp_text({"data": {"restaurants": []}}) == ""
        assert mcp_text("nope") == ""
        assert mcp_text(None) == ""


class TestParseRestaurantList:
    def test_parses_names_ids_ratings(self):
        out = parse_restaurant_list(DINEOUT)
        rows = out["data"]["restaurants"]
        assert [r["name"] for r in rows] == [
            "Farzi Cafe",
            "Barkaas Indo Arabic Restaurant",
            "The Great Kabab Factory",
        ]
        assert [r["id"] for r in rows] == ["824088", "824099", "824111"]
        assert rows[0]["rating"] == 4.6
        assert rows[1]["rating"] == 4.8
        assert out["data"]["source"] == "swiggy-mcp-text"

    def test_locality_and_distance(self):
        rows = parse_restaurant_list(DINEOUT)["data"]["restaurants"]
        assert rows[0]["locality"] == "Hazratganj"
        assert rows[2]["locality"] == "Gomti Nagar"
        assert rows[1]["distanceKm"] == 2.3
        assert "distanceKm" not in rows[0]

    def test_original_order_kept_as_rank(self):
        rows = parse_restaurant_list(DINEOUT)["data"]["restaurants"]
        assert [r["_rank"] for r in rows] == [0, 1, 2]

    def test_mock_response_passes_through_as_none(self):
        assert parse_restaurant_list({"data": {"restaurants": [{"name": "x"}]}}) is None

    def test_error_and_empty_are_none(self):
        assert parse_restaurant_list(_env("")) is None
        assert parse_restaurant_list(_env("Found 0 restaurants.")) is None
        assert parse_restaurant_list({"error": "boom"}) is None

    def test_price_and_minutes_when_present(self):
        env = _env("1. Pizza Place — | 4.1★ | ₹1,200 for two | 25 min | Andheri (ID: p9)")
        r = parse_restaurant_list(env)["data"]["restaurants"][0]
        assert r["costForTwo"] == 1200
        assert r["deliveryTimeMinutes"] == 25
        assert r["locality"] == "Andheri"

    def test_name_with_hyphen_or_ampersand(self):
        env = _env(
            "1. Hard Rock Cafe - Saket — | 4.5★ | | Saket (ID: h1)\n"
            "2. Barbeque & Grill — | 4.2★ | | HSR (ID: b2)"
        )
        rows = parse_restaurant_list(env)["data"]["restaurants"]
        assert rows[0]["name"] == "Hard Rock Cafe - Saket"
        assert rows[1]["name"] == "Barbeque & Grill"

    def test_alt_id_format(self):
        env = _env("1. Some Place — 4.0★ (id = XYZ99)")
        r = parse_restaurant_list(env)["data"]["restaurants"][0]
        assert r["id"] == "XYZ99"
