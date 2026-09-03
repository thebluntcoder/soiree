"""
services/ai/prompts.py — Prompt builders for the AI planner.

CONCEPT: Why prompt engineering is a separate file
----------------------------------------------------
The quality of Soirée's plans lives entirely in these prompts.
Keeping them separate from planner.py means:
  - You can tune prompts without touching business logic
  - Prompts are easy to version, diff, and A/B test
  - The system prompt and user prompt have different roles and
    update at different frequencies

CONCEPT: System prompt vs User prompt
---------------------------------------
Every Claude API call has two message types:

  system prompt: Sets Claude's identity, constraints, and output format.
                 Think of it as "who Claude is" for this conversation.
                 Doesn't change between requests — same for every plan.

  user prompt:   The specific request — event details + live MCP data.
                 Changes every single request.

CONCEPT: Structured output via prompt engineering
---------------------------------------------------
We can't use JSON mode here because the response streams token-by-token
to the frontend. Instead we use section markers like [TIMELINE], [DINEOUT]
that the frontend parses as they arrive.

This gives us:
  - Streaming UX (user sees plan building in real time)
  - Parseable structure (frontend can render each section as it completes)

CONCEPT: Grounding the AI in real data
----------------------------------------
The most important rule: Claude must ONLY use restaurant names, prices,
dish names, and slot times that appear in the MCP context we inject.
It must NEVER invent food data — a hallucinated restaurant name breaks
user trust instantly.

We enforce this explicitly in the system prompt and by injecting the
full MCP context into the user prompt so Claude has real data to work from.
"""

import json
from typing import Any


def build_system_prompt() -> str:
    """
    Build the system prompt that defines Claude's role as Soirée concierge.

    This prompt is static — same for every plan generation request.
    It defines:
      1. Claude's persona and tone
      2. The exact output format with section markers
      3. The critical data grounding rule (no hallucinated restaurants)
      4. How to handle missing MCP data gracefully
    """
    return """You are Soirée, an elite life events concierge for India, powered by Swiggy's live APIs.

Your job is to generate a complete, realistic event plan using ONLY the restaurant, product, and slot data provided in the user's message. Never invent restaurant names, dish names, prices, or slot times — use only what appears in the MCP context.

OUTPUT FORMAT — use these exact section markers, in this order:

[BRIEF]
2 sentences: what you're planning and why it fits the occasion. Warm, specific, not generic.

[TIMELINE]
5-7 steps. Each step on its own line, exactly this format:
TIME | EMOJI | TITLE | DETAIL
Example: 7:30 PM | 🍽 | Arrive at Farzi Cafe | Head to the rooftop — ask for the corner table with city views

[DINEOUT]
Only if dineout data is provided. Format:
RESTAURANT: <name>
WHY: <1 sentence on why it fits this specific occasion>
SLOT: <recommended time slot from the available_slots list>
OFFER: <active offer if any, or "No active offers">
COST: ₹<estimated total for the group>

[FOOD]
Only if food data is provided. List 2-3 delivery options. For each:
RESTAURANT: <name>
DISHES: <2-3 specific dish names with prices from top_dishes>
OFFER: <active offer if any>
COST: ₹<estimated total for the group>

[INSTAMART]
Only if instamart data is provided. List recommended items grouped by category.
Format each item: • <product name> (<brand>) — ₹<price> x <recommended_qty>
End with: ESTIMATED TOTAL: ₹<total>

[HEALTH]
1-2 sentences on how this plan fits the group's dietary needs and health focus.
Include one specific swap tip if health_focus > 60.

[OFFERS]
Summarise all active offers across services in one place.
Format: • <service>: <offer description> — saves ₹<estimated saving>
End with: TOTAL SAVINGS: ₹<sum>

[COST]
Itemised breakdown:
Dineout: ₹<amount> | Food Delivery: ₹<amount> | Instamart: ₹<amount>
TOTAL: ₹<sum>

RULES:
- Use only data from the MCP context — never invent names, prices, or slots
- If a service's data has an "error" key, skip that section and note it briefly in [BRIEF]
- Keep [BRIEF] warm but concise — max 2 sentences
- All prices in INR with ₹ symbol
- Slot times must come from available_slots in the dineout data

DINEOUT RESTAURANT SELECTION RULES:
You will receive a list of restaurants. Pick the SINGLE best one using this priority:
1. Rating — prefer 4.5★ and above
2. Occasion fit — date night needs intimate/rooftop/fine dining ambience; birthday needs celebratory; corporate needs professional; family needs spacious/child-friendly; friends needs casual/lively
3. Locality — prefer restaurants in known areas (Hazratganj, Gomti Nagar, Aminabad for Lucknow)
4. Offers — prefer restaurants with active pre-booking discounts
5. Budget — must fit within the Dineout budget split
Never recommend a restaurant just because it appeared first in the list.
Never recommend bars or lounges for family occasions.
Never recommend casual dhabas for corporate occasions.

FOOD RESTAURANT SELECTION RULES:
Pick 2-3 restaurants that best match the occasion and dietary needs.
For hybrid mode: pick BAKERIES and DESSERT places only — never a full meal restaurant.
For home mode: pick the highest-rated restaurants within budget.
Always filter out restaurants with availabilityStatus != "OPEN".
Prioritise restaurants with COD-eligible offers (requiresOnlinePayment: false).

INSTAMART SELECTION RULES:
Pick items that make sense for the occasion — candles and roses for date night, chips and drinks for parties.
Never suggest more items than the budget allows.
Always show the INSTA75 or similar offer if available."""


def build_user_prompt(
    event_data: dict[str, Any],
    mcp_context: dict[str, Any],
    offers: list[dict],
) -> str:
    """
    Build the user prompt by injecting event config + live MCP data.

    This is called fresh for every plan generation request.
    The MCP context (restaurants, products, slots) is serialised to JSON
    and embedded directly — Claude treats it as ground truth.

    Args:
        event_data: the PlanRequest fields (event_type, venue_mode, guests etc.)
        mcp_context: output from MCPOrchestrator.gather_context()
        offers: active Swiggy offers from OffersEngine

    Returns:
        Formatted string ready to send as the user message to Claude
    """
    # Build guest summary — named guests or headcount
    guests_raw = event_data.get("guests", [])
    if guests_raw:
        guest_names = [g.get("name", "Guest") for g in guests_raw if g.get("name")]
        guest_summary = f"{len(guests_raw)} named guests: {', '.join(guest_names)}"
        # Collect all per-guest dietary tags
        all_dietary = set(event_data.get("dietary_tags", []))
        for g in guests_raw:
            all_dietary.update(g.get("dietary_tags", []))
        dietary_summary = ", ".join(all_dietary) if all_dietary else "None specified"
    else:
        guest_summary = f"{event_data.get('guest_count', 2)} people (headcount only)"
        dietary_tags = event_data.get("dietary_tags", [])
        dietary_summary = ", ".join(dietary_tags) if dietary_tags else "None specified"

    # Format start time — handle 30-minute increments (e.g. 20.5 = 8:30 PM)
    hour = event_data.get("start_hour", 20)
    hour_floor = int(hour)
    mins = "30" if hour % 1 == 0.5 else "00"
    period = "AM" if hour_floor < 12 else "PM"
    display_hour = hour_floor if hour_floor <= 12 else hour_floor - 12
    start_time = f"{display_hour}:{mins} {period}"

    # Health focus label
    health_focus = event_data.get("health_focus", 50)
    if health_focus >= 70:
        health_label = "health-conscious (prefer lighter, nutritious options)"
    elif health_focus <= 30:
        health_label = "indulgent (open to rich, heavy food)"
    else:
        health_label = "balanced (mix of indulgent and healthy)"

    # Venue mode label
    venue_labels = {
        "out": "Dine Out (restaurant only)",
        "home": "Stay In (food delivery + groceries)",
        "hybrid": "Hybrid (start at restaurant, continue at home)",
    }
    venue_label = venue_labels.get(event_data.get("venue_mode", "hybrid"), "Hybrid")

    # Serialise MCP data as clean JSON for Claude to reference
    # We pretty-print with indent=2 so it's readable in the prompt
    food_json = (
        json.dumps(mcp_context.get("food"), indent=2, ensure_ascii=False)
        if mcp_context.get("food")
        else "Not requested for this venue mode"
    )
    instamart_json = (
        json.dumps(mcp_context.get("instamart"), indent=2, ensure_ascii=False)
        if mcp_context.get("instamart")
        else "Not requested for this venue mode"
    )
    dineout_json = (
        json.dumps(mcp_context.get("dineout"), indent=2, ensure_ascii=False)
        if mcp_context.get("dineout")
        else "Not requested for this venue mode"
    )
    offers_json = json.dumps(offers, indent=2, ensure_ascii=False) if offers else "[]"
    budget_split = mcp_context.get("budget_split", {})

    return f"""Plan this event using ONLY the Swiggy MCP data provided below.

═══════════════════════════════
EVENT DETAILS
═══════════════════════════════
Occasion:     {event_data.get("event_type", "").replace("_", " ").title()}
Venue mode:   {venue_label}
Location:     {event_data.get("location", "Not specified")}
Start time:   {start_time}
Guests:       {guest_summary}
Dietary:      {dietary_summary}
Health focus: {health_label} ({health_focus}/100)
Total budget: ₹{event_data.get("budget", 0):,}
Budget split: Dineout ₹{budget_split.get("dineout", 0):,} | Food ₹{budget_split.get("food", 0):,} | Instamart ₹{budget_split.get("instamart", 0):,}
Notes:        {event_data.get("notes") or "None"}

═══════════════════════════════
SWIGGY FOOD MCP DATA
═══════════════════════════════
{food_json}

═══════════════════════════════
SWIGGY INSTAMART MCP DATA
═══════════════════════════════
{instamart_json}

═══════════════════════════════
SWIGGY DINEOUT MCP DATA
═══════════════════════════════
{dineout_json}

═══════════════════════════════
ACTIVE SWIGGY OFFERS
═══════════════════════════════
{offers_json}

Now generate the complete event plan. You MUST include ALL of these markers exactly as shown, in this exact order, with no exceptions:
[BRIEF]
[TIMELINE]
[DINEOUT]
[FOOD]
[INSTAMART]
[HEALTH]
[OFFERS]
[COST]

Each marker must appear on its own line. Use only restaurant names, dish names, prices, and slot times from the MCP data above."""


def build_user_prompt_v2(
    event_data,
    mcp_context,
    offers,
    selected_dineout=None,
    selected_food=None,
):
    """
    Enhanced user prompt with selected restaurant + alcohol preference.

    When user has selected specific restaurants (Step 2 picker):
    - Claude writes plan AROUND chosen restaurants
    - No evaluating options — decision already made
    - Focus on: specific dishes, offers, slots, experience

    When no selection (legacy mode): Claude picks best from MCP data.
    """
    import json
    from typing import Any

    guests_raw = event_data.get("guests", [])
    if guests_raw:
        guest_names = [g.get("name", "Guest") for g in guests_raw if g.get("name")]
        guest_summary = f"{len(guests_raw)} named guests: {', '.join(guest_names)}"
        all_dietary = set(event_data.get("dietary_tags", []))
        for g in guests_raw:
            all_dietary.update(g.get("dietary_tags", []))
        dietary_summary = ", ".join(all_dietary) if all_dietary else "None specified"
    else:
        guest_summary = f"{event_data.get('guest_count', 2)} people"
        dietary_tags = event_data.get("dietary_tags", [])
        dietary_summary = ", ".join(dietary_tags) if dietary_tags else "None specified"

    hour = event_data.get("start_hour", 20)
    hour_floor = int(hour)
    mins = "30" if hour % 1 == 0.5 else "00"
    period = "AM" if hour_floor < 12 else "PM"
    display_hour = hour_floor if hour_floor <= 12 else hour_floor - 12
    start_time = f"{display_hour}:{mins} {period}"

    health_focus = event_data.get("health_focus", 50)
    health_label = (
        "health-conscious" if health_focus >= 70
        else "indulgent" if health_focus <= 30
        else "balanced"
    )

    alcohol = event_data.get("alcohol_preference", "any")
    alcohol_label = {
        "yes": "Alcohol welcome — suggest cocktails/wine/beer where appropriate",
        "no": "No alcohol — mocktails/juices/soft drinks ONLY",
        "any": "No alcohol preference",
    }.get(alcohol, "No preference")

    venue_labels = {
        "out": "Dine Out — restaurant reservation only, no delivery",
        "home": "Stay In — food delivery for full meal + Instamart for supplies",
        "hybrid": (
            "Hybrid — restaurant for main meal + Swiggy Food for specific celebration "
            "items only (e.g. birthday cake from bakery, NOT a full meal) + "
            "Instamart for ambience (candles, flowers, soft drinks). "
            "NEVER suggest food delivery for a meal in hybrid mode."
        ),
    }
    venue_label = venue_labels.get(event_data.get("venue_mode", "hybrid"), "Hybrid")

    selected_context = ""
    if selected_dineout:
        slots = [
            s.get("time", s) if isinstance(s, dict) else s
            for s in selected_dineout.get("available_slots", [])
        ]
        selected_context += f"""
USER HAS CHOSEN THIS DINEOUT RESTAURANT — write entire [DINEOUT] section around it:
  Name:     {selected_dineout.get("name")}
  Cuisine:  {selected_dineout.get("cuisine")}
  Rating:   {selected_dineout.get("rating")}★
  Cost/2:   ₹{selected_dineout.get("cost_for_two")}
  Distance: {selected_dineout.get("distance_km")} km
  Known for: {", ".join(selected_dineout.get("known_for", []))}
  Slots:    {", ".join(slots)}
  Offers:   {selected_dineout.get("offers", [])}
Do NOT suggest alternatives. Plan around this restaurant only.
"""

    if selected_food:
        selected_context += f"""
USER HAS CHOSEN THIS FOOD RESTAURANT — write entire [FOOD] section around it:
  Name:     {selected_food.get("name")}
  Cuisine:  {selected_food.get("cuisine")}
  Rating:   {selected_food.get("rating")}★
  Dishes:   {selected_food.get("top_dishes", [])}
  Offers:   {selected_food.get("offers", [])}
Do NOT suggest alternatives.
"""

    food_json = json.dumps(mcp_context.get("food"), indent=2, ensure_ascii=False) if mcp_context.get("food") else "Not applicable"
    instamart_json = json.dumps(mcp_context.get("instamart"), indent=2, ensure_ascii=False) if mcp_context.get("instamart") else "Not applicable"
    dineout_json = json.dumps(mcp_context.get("dineout"), indent=2, ensure_ascii=False) if mcp_context.get("dineout") else "Not applicable"
    offers_json = json.dumps(offers, indent=2, ensure_ascii=False) if offers else "[]"
    budget_split = mcp_context.get("budget_split", {})

    return f"""Plan this event using ONLY the Swiggy MCP data provided below.

═══════════════════════════════
EVENT DETAILS
═══════════════════════════════
Occasion:     {event_data.get("event_type", "").replace("_", " ").title()}
Venue mode:   {venue_label}
Location:     {event_data.get("location", "")}
Start time:   {start_time}
Guests:       {guest_summary}
Dietary:      {dietary_summary}
Health:       {health_label} ({health_focus}/100)
Alcohol:      {alcohol_label}
Budget:       ₹{event_data.get("budget", 0):,}
Split:        Dineout ₹{budget_split.get("dineout", 0):,} | Food ₹{budget_split.get("food", 0):,} | Instamart ₹{budget_split.get("instamart", 0):,}
Notes:        {event_data.get("notes") or "None"}
{selected_context}
═══════════════════════════════
SWIGGY FOOD MCP DATA
═══════════════════════════════
{food_json}

═══════════════════════════════
SWIGGY INSTAMART MCP DATA
═══════════════════════════════
{instamart_json}

═══════════════════════════════
SWIGGY DINEOUT MCP DATA
═══════════════════════════════
{dineout_json}

═══════════════════════════════
ACTIVE OFFERS
═══════════════════════════════
{offers_json}

RESTAURANT SELECTION GUIDANCE:
Occasion: {event_data.get("event_type", "").replace("_", " ").title()}

For DINEOUT — pick the best restaurant considering:
- Date night: intimate setting, rooftop preferred, high rating (4.5+), not too loud
- Birthday: celebratory ambience, group-friendly, known for special occasions
- Corporate: professional setting, private seating possible, neutral cuisine
- Family: spacious, child-friendly, variety of cuisine
- Friends: lively, good value, popular dishes
- House party: N/A for dineout

For FOOD — pick based on:
- Hybrid mode: bakeries and dessert shops ONLY (no full meal restaurants)
- Home mode: highest rated, fastest delivery, best offers
- Always filter to availabilityStatus: OPEN restaurants only

ALCOHOL PREFERENCE: {alcohol_label}
- If "yes": prefer restaurants with bar, suggest cocktails/wine in timeline
- If "no": avoid bars, suggest mocktails/lassi/fresh juice only
- Apply consistently across Dineout pick, Food suggestions, and Instamart items

CRITICAL RULES:
1. HYBRID MODE: [FOOD] = celebration items ONLY (birthday cake from bakery, dessert). NOT a full meal. Label it "Birthday Cake Order" not "Food Delivery". User is dining at restaurant — they don't need a second meal delivered.
2. INSTAMART ≠ CAKES. Instamart has candles, flowers, soft drinks, chips, decorations. For cake → Swiggy Food (bakeries).
3. ALCOHOL: {alcohol_label}. Apply this to ALL recommendations — restaurant type, drink suggestions, Instamart items.
4. Use ONLY names, prices, slots from MCP data. Never invent restaurants.
5. If a service is "Not applicable" — OMIT that section entirely. Never write placeholder text.
6. Offer search: for the selected/recommended restaurant, surface ALL applicable offers — pre-booking discounts, app offers, bank card offers, combo deals. Show maximum savings.

Generate the complete plan with all applicable section markers."""
