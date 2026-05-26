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
- Be specific to the occasion — a date night plan should feel romantic, corporate should feel professional
- Keep [BRIEF] warm but concise — max 2 sentences
- All prices in INR with ₹ symbol
- Slot times must come from available_slots in the dineout data"""


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
