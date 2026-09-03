"""
lib/parse_plan.py — Server-side plan text parser.

CONCEPT: Why parse on the backend too?
----------------------------------------
The frontend parses plan text for rendering.
The backend needs to parse it for DB storage — extracting
costs as integers, timeline as JSON, sections as text fields.

This mirrors frontend's parsePlan.ts but in Python.
Both use the same section marker pattern: [SECTION_NAME]

CONCEPT: ⏎ decoding
---------------------
The planner encodes newlines as ⏎ before SSE transmission.
By the time text reaches this parser it still has ⏎ symbols.
We decode them back to \n first before extracting sections.
"""

import re
from typing import Any


def get_section(text: str, marker: str) -> str:
    """
    Extract content between [MARKER] and the next [MARKER] or end of string.

    Args:
        text:   full plan text with section markers
        marker: section name e.g. 'BRIEF', 'TIMELINE', 'DINEOUT'

    Returns:
        section content as string, stripped of leading/trailing whitespace
    """
    pattern = rf"\[{marker}\]\n?([\s\S]*?)(?=\n?\[[A-Z]+\]|$)"
    match = re.search(pattern, text)
    return match.group(1).strip() if match else ""


def parse_timeline(raw: str) -> list[dict]:
    """
    Parse timeline section into list of step dicts.

    Each line format: TIME | EMOJI | TITLE | DETAIL
    Lines without | are skipped.

    Returns list of dicts: [{time, emoji, title, detail}]
    """
    steps = []
    for line in raw.split("\n"):
        if "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) >= 3 and parts[2]:
            steps.append(
                {
                    "time": parts[0] if len(parts) > 0 else "",
                    "emoji": parts[1] if len(parts) > 1 else "●",
                    "title": parts[2] if len(parts) > 2 else "",
                    "detail": parts[3] if len(parts) > 3 else "",
                }
            )
    return steps


def extract_cost(text: str, pattern: str) -> str:
    """
    Extract a cost string matching pattern from text.
    Returns empty string if not found.

    Example: extract_cost("TOTAL: 2417", "TOTAL: ([0-9,]+)") returns "2417"
    """
    match = re.search(pattern, text)
    return match.group(1) if match else ""


def _service_cost(cost_section: str, label: str) -> str:
    """
    Pull one service's cost out of the [COST] section.

    The section is meant to read:
        Dineout: ₹1,275 | Food Delivery: ₹400 | Instamart: ₹149
        TOTAL: ₹1,824
    but the model is not perfectly consistent — it may split the line,
    annotate it ("Dineout (Farzi Cafe): ₹1,530 after discount"), or drop
    a service entirely when it isn't part of the plan. So: find the first
    non-TOTAL line that mentions `label` and take the first ₹ amount on it.

    `label` is "Dineout", "Food" or "Instamart".
    Returns "" when the service isn't in the breakdown.
    """
    # Split on newlines AND pipes so "A: ₹1 | B: ₹2" is two segments.
    for segment in re.split(r"[|\n]", cost_section):
        if segment.strip().upper().startswith("TOTAL"):
            continue
        if re.search(rf"\b{label}\b", segment, re.IGNORECASE):
            amount = re.search(r"₹\s*([\d,]+)", segment)
            if amount:
                return "₹" + amount.group(1)
    return ""


def parse_plan_text(raw_text: str) -> dict[str, Any]:
    """
    Parse full plan text into structured dict for DB storage.

    Decodes ⏎ → \n first, then extracts each section.
    Returns dict matching the fields in Plan model.

    Args:
        raw_text: accumulated SSE text with ⏎ encoded newlines

    Returns:
        dict with keys: brief, timeline, dineout, food, instamart,
                        health, offers, cost, totalCost, totalSavings,
                        dineoutCost, foodCost, instamartCost
    """
    # Decode ⏎ proxy characters back to newlines
    # Then strip SSE "data: " prefixes if any leaked through
    cleaned = raw_text.replace("⏎", "\n")
    cleaned = re.sub(r"^data:\s*", "", cleaned, flags=re.MULTILINE)

    brief = get_section(cleaned, "BRIEF")
    timeline = get_section(cleaned, "TIMELINE")
    dineout = get_section(cleaned, "DINEOUT")
    food = get_section(cleaned, "FOOD")
    instamart = get_section(cleaned, "INSTAMART")
    health = get_section(cleaned, "HEALTH")
    offers = get_section(cleaned, "OFFERS")
    cost = get_section(cleaned, "COST")

    return {
        "brief": brief,
        "timeline": parse_timeline(timeline),
        "dineout": dineout,
        "food": food,
        "instamart": instamart,
        "health": health,
        "offers": offers,
        "cost": cost,
        "totalCost": extract_cost(cost, r"TOTAL:\s*(₹[\d,]+)"),
        "totalSavings": extract_cost(offers, r"TOTAL SAVINGS:\s*(₹[\d,]+)"),
        "dineoutCost": _service_cost(cost, "Dineout"),
        "foodCost": _service_cost(cost, "Food"),
        "instamartCost": _service_cost(cost, "Instamart"),
    }
