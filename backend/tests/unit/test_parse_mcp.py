"""
tests/unit/test_parse_mcp.py — normalising Swiggy MCP search responses.

Fixtures below are trimmed from real `GET /search/_debug` output:
Food packs a JSON blob in its text field; Dineout sends numbered text lines.
"""

from app.services.mcp.parse_mcp import mcp_text, parse_restaurant_list


def _env(text: str) -> dict:
    return {"result": {"content": [{"type": "text", "text": text}]}}


# ── real Food response: JSON blob + a trailing "widget" note ─────────────────
FOOD = _env(
    '{"restaurants":['
    '{"id":"78675","name":"Raj Luxmi Restaurant (Ad)",'
    '"cuisines":["Indian","South Indian","Chinese","Thalis"],"avgRating":4.3,'
    '"totalRatings":"55K+","costForTwo":"₹300 for two","areaName":"Charbagh",'
    '"distanceKm":6.9,"deliveryTimeMinutes":39,"deliveryTimeRange":"35-45 MINS",'
    '"veg":true,"offer":"₹125 OFF ABOVE ₹199",'
    '"imageUrl":"https://x/78675.jpg","availabilityStatus":"OPEN"},'
    '{"id":"572346","name":"Bhargavas Restaurant & Banquet",'
    '"cuisines":["North Indian","Chinese","Snacks"],"avgRating":4.4,'
    '"costForTwo":"₹300 for two","areaName":"LDA Colony","distanceKm":1.4,'
    '"deliveryTimeMinutes":20,"offer":"70% OFF UPTO ₹140",'
    '"availabilityStatus":"OPEN"}'
    '],"dishes":[],"total":10,"totalRestaurants":236,"hasMore":true}\n\n'
    "⚠️ A rich UI widget may be shown to the user with this data."
)

# ── real Dineout response: numbered lines + coordinates line ─────────────────
DINEOUT = _env(
    'Found 39 restaurant(s) matching "restaurant", showing 10. 29 more available, '
    "call again with offset=10. Some results are non-participating restaurants: "
    "no table booking or slots available on Dineout for those.\n"
    "1. Dwarka Restaurant —  | 4.3★ |  | Dwarka (ID: 32544)\n"
    "2. I For Her Restaurant —  | 0★ |  | Janakpuri (ID: 1114232)\n"
    "3. Begeterre - Museum Themed Restaurant —  | 4.4★ |  | Sector 43 (ID: 1014169)\n"
    "6. Kwality Restaurant —  | 4.5★ |  | DLF Cyber City (ID: 1369973)\n"
    "10. Rhum Bar and Restaurant —  | 4.5★ |  | Sector 66 (ID: 821428)\n"
    "Search coordinates: latitude=28.492222, longitude=77.0782287 (use these for "
    "get_restaurant_details and downstream calls).\n\n"
    "When the user selects a restaurant, call get_restaurant_details..."
)


class TestMcpText:
    def test_non_envelope_is_empty(self):
        assert mcp_text({"data": {"restaurants": []}}) == ""
        assert mcp_text("nope") == ""
        assert mcp_text(None) == ""


class TestFoodJson:
    def test_parses_and_normalises(self):
        rows = parse_restaurant_list(FOOD)["data"]["restaurants"]
        assert [r["name"] for r in rows] == [
            "Raj Luxmi Restaurant",  # "(Ad)" stripped
            "Bhargavas Restaurant & Banquet",
        ]
        r0 = rows[0]
        assert r0["id"] == "78675"
        assert r0["rating"] == 4.3
        assert r0["cuisine"] == "Indian, South Indian, Chinese"
        assert r0["priceForTwo"] == 300
        assert r0["locality"] == "Charbagh"
        assert r0["distanceKm"] == 6.9
        assert r0["deliveryTimeMinutes"] == 39
        assert r0["veg"] is True
        assert r0["offers"] == [{"description": "₹125 OFF ABOVE ₹199"}]
        assert r0["imageUrl"].endswith("78675.jpg")
        assert r0["sponsored"] is True

    def test_source_tag(self):
        assert parse_restaurant_list(FOOD)["data"]["source"] == "swiggy-food-json"

    def test_area_name_question_marks_cleaned(self):
        env = _env('{"restaurants":[{"id":"1","name":"X","areaName":"emerald?mall?lucknow"}]}')
        assert parse_restaurant_list(env)["data"]["restaurants"][0]["locality"] == (
            "emerald mall lucknow"
        )


class TestDineoutText:
    def test_parses_lines(self):
        out = parse_restaurant_list(DINEOUT)
        rows = out["data"]["restaurants"]
        assert [r["name"] for r in rows] == [
            "Dwarka Restaurant",
            "I For Her Restaurant",
            "Begeterre - Museum Themed Restaurant",  # hyphen in name kept
            "Kwality Restaurant",
            "Rhum Bar and Restaurant",
        ]
        assert [r["id"] for r in rows] == [
            "32544", "1114232", "1014169", "1369973", "821428",
        ]

    def test_zero_star_is_unrated(self):
        rows = parse_restaurant_list(DINEOUT)["data"]["restaurants"]
        assert rows[0]["rating"] == 4.3
        assert "rating" not in rows[1]  # "0★" dropped, not stored as 0.0

    def test_locality(self):
        rows = parse_restaurant_list(DINEOUT)["data"]["restaurants"]
        assert rows[3]["locality"] == "DLF Cyber City"

    def test_search_coordinates_extracted(self):
        coords = parse_restaurant_list(DINEOUT)["data"]["coordinates"]
        assert coords == {"lat": 28.492222, "lng": 77.0782287}

    def test_source_tag(self):
        assert parse_restaurant_list(DINEOUT)["data"]["source"] == "swiggy-dineout-text"

    def test_original_order_kept_as_rank(self):
        rows = parse_restaurant_list(DINEOUT)["data"]["restaurants"]
        assert [r["_rank"] for r in rows] == [0, 1, 2, 3, 4]


class TestPassThrough:
    def test_mock_dict_is_none(self):
        assert parse_restaurant_list({"data": {"restaurants": [{"name": "x"}]}}) is None

    def test_error_and_empty_are_none(self):
        assert parse_restaurant_list(_env("")) is None
        assert parse_restaurant_list(_env("Found 0 restaurants.")) is None
        assert parse_restaurant_list({"error": "boom"}) is None
        assert parse_restaurant_list(_env('{"total":0,"restaurants":[]}')) is None
