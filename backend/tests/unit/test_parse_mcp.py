"""
tests/unit/test_parse_mcp.py — normalising Swiggy MCP search responses.

Fixtures below are trimmed from real `GET /search/_debug` output:
Food packs a JSON blob in its text field; Dineout sends numbered text lines.
"""

from app.services.mcp.parse_mcp import (
    closest_slot,
    mcp_text,
    parse_available_slots,
    parse_restaurant_details,
    parse_restaurant_list,
)


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

    def test_has_more_from_total(self):
        d = parse_restaurant_list(FOOD)["data"]
        assert d["hasMore"] is True  # totalRestaurants 236 > 2 shown
        assert d["totalResults"] == 236

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

    def test_has_more_and_total_from_header(self):
        d = parse_restaurant_list(DINEOUT)["data"]
        assert d["totalResults"] == 39  # "Found 39 restaurant(s)"
        assert d["hasMore"] is True     # "29 more available"

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


# ── real get_restaurant_details: "Key: Value" text ──────────────────────────
DETAILS = _env(
    "Restaurant: Dwarka Restaurant\n"
    "Restaurant ID: 32544\n"
    "Cuisines: North Indian, Chinese\n"
    "Address: 15.2 km • 1st floor, plot 5, Sector 10, Dwarka, Delhi\n"
    "Rating: 4.3\n"
    "Cost for two: ₹500 for two\n"
    "Timings: Open till 11PM\n"
    "Coordinates: latitude=28.492222, longitude=77.0782287\n"
    "Offers: Flat 25% off on Total Bill; Flat 20% off on Total Bill; "
    "Flat 20% off on total bill\n"
    "Amenities / Highlights: Reservation available, Parking available, Free wifi\n\n"
    "When the user wants to book a table...\n"
    "⚠️ A rich UI widget may be shown."
)


class TestRestaurantDetails:
    def test_key_value_fields(self):
        d = parse_restaurant_details(DETAILS)
        assert d["name"] == "Dwarka Restaurant"
        assert d["id"] == "32544"
        assert d["cuisine"] == "North Indian, Chinese"
        assert d["rating"] == 4.3
        assert d["cost_for_two"] == 500
        assert d["timings"] == "Open till 11PM"

    def test_address_distance_split(self):
        d = parse_restaurant_details(DETAILS)
        assert d["distance_km"] == 15.2
        assert d["address"] == "1st floor, plot 5, Sector 10, Dwarka, Delhi"

    def test_offers_deduped(self):
        d = parse_restaurant_details(DETAILS)
        descs = [o["description"] for o in d["offers"]]
        assert descs == ["Flat 25% off on Total Bill", "Flat 20% off on Total Bill"]

    def test_amenities_list(self):
        d = parse_restaurant_details(DETAILS)
        assert d["amenities"] == [
            "Reservation available", "Parking available", "Free wifi"
        ]

    def test_coordinates(self):
        assert parse_restaurant_details(DETAILS)["coordinates"] == {
            "lat": 28.492222, "lng": 77.0782287
        }

    def test_non_detail_is_none(self):
        assert parse_restaurant_details({"data": {}}) is None
        assert parse_restaurant_details(_env("just some prose with no colon fields")) is None
        assert parse_restaurant_details(_env("Foo: bar\nBaz: qux")) is None  # no "Restaurant:"
        assert parse_restaurant_list(_env('{"total":0,"restaurants":[]}')) is None


# get_available_slots' real text format is UNCONFIRMED (no live token to test
# against — see parse_mcp.py's comment above parse_available_slots). These
# fixtures are best-guess shapes following Dineout's established
# "one line per item" convention, tolerant of either.
SLOTS_STATUS_MARKED = _env(
    "7:00 PM - Available\n"
    "7:30 PM - Booked\n"
    "8:00 PM - Available\n"
    "8:30 PM - Sold out\n"
    "9:00 PM - Available"
)
SLOTS_ONLY_AVAILABLE_LISTED = _env(
    "Available slots for 2026-05-10:\n"
    "1. 7:30 PM (ID: slot_1930)\n"
    "2. 8:00 PM (ID: slot_2000)\n"
    "3. 8:30 PM (ID: slot_2030)"
)


class TestAvailableSlots:
    def test_status_marked_filters_unavailable(self):
        slots = parse_available_slots(SLOTS_STATUS_MARKED)
        assert [s["time"] for s in slots] == ["7:00 PM", "8:00 PM", "9:00 PM"]
        assert all(s["available"] for s in slots)

    def test_only_available_listed_no_status_word(self):
        slots = parse_available_slots(SLOTS_ONLY_AVAILABLE_LISTED)
        assert [s["time"] for s in slots] == ["7:30 PM", "8:00 PM", "8:30 PM"]

    def test_slot_id_captured_when_present(self):
        slots = parse_available_slots(SLOTS_ONLY_AVAILABLE_LISTED)
        assert slots[0]["slotId"] == "slot_1930"

    def test_slot_id_absent_is_fine(self):
        slots = parse_available_slots(SLOTS_STATUS_MARKED)
        assert "slotId" not in slots[0]

    def test_no_times_is_none(self):
        assert parse_available_slots(_env("No availability for this restaurant today.")) is None
        assert parse_available_slots({"data": {}}) is None

    def test_all_unavailable_is_none(self):
        assert parse_available_slots(_env("7:00 PM - Booked\n7:30 PM - Full")) is None


class TestClosestSlot:
    SLOTS = [
        {"time": "7:00 PM", "available": True},
        {"time": "8:00 PM", "available": True},
        {"time": "9:30 PM", "available": True},
    ]

    def test_picks_nearest(self):
        assert closest_slot(self.SLOTS, 20.0)["time"] == "8:00 PM"
        assert closest_slot(self.SLOTS, 19.1)["time"] == "7:00 PM"
        assert closest_slot(self.SLOTS, 21.4)["time"] == "9:30 PM"

    def test_exact_match(self):
        assert closest_slot(self.SLOTS, 19.0)["time"] == "7:00 PM"

    def test_ignores_unavailable_slots(self):
        slots = [{"time": "8:00 PM", "available": False}, {"time": "9:00 PM", "available": True}]
        assert closest_slot(slots, 20.0)["time"] == "9:00 PM"

    def test_am_pm_and_midnight_noon(self):
        slots = [{"time": "12:00 AM"}, {"time": "12:30 PM"}]
        assert closest_slot(slots, 0.0)["time"] == "12:00 AM"
        assert closest_slot(slots, 12.5)["time"] == "12:30 PM"

    def test_empty_list_is_none(self):
        assert closest_slot([], 20.0) is None

    def test_all_unavailable_is_none(self):
        assert closest_slot([{"time": "8:00 PM", "available": False}], 20.0) is None

    def test_unparseable_time_skipped(self):
        slots = [{"time": "whenever"}, {"time": "9:00 PM"}]
        assert closest_slot(slots, 20.0)["time"] == "9:00 PM"
