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

CONCEPT: [COST] is structured, everything else is prose
-----------------------------------------------------------
Every other section is free text the model writes and we display
as-is. [COST] used to be free text too ("Dineout: ₹X | Food: ₹Y"),
parsed with regex tolerant of the model's own formatting drift
(extra notes, a dropped pipe, an unexpected word order) — tolerant,
but still silently wrong whenever the model's prose diverged from what
the regex expected. It's now a single-line JSON object the model emits
directly (see prompts.py's [COST] spec) — parse_cost_block() just
json.loads() it, with a bit of tolerance for stray whitespace or a
wrapping code fence, not for arbitrary prose.
"""

import json
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


_COST_KEYS = ("dineout", "food", "instamart", "total")


def parse_cost_block(cost_section: str) -> dict[str, int | None]:
    """
    Parse the [COST] section — a single-line JSON object, e.g.
    {"dineout": 1275, "food": 400, "instamart": 149, "total": 1824}.

    A missing key means that service wasn't part of the plan (None, not
    an error). Tolerant of the model wrapping the JSON in a ```json fence
    or a stray sentence around it — extracts the first {...} block and
    parses just that, not the whole section.

    Returns {"dineout", "food", "instamart", "total"}, each int | None.
    """
    empty: dict[str, int | None] = {k: None for k in _COST_KEYS}
    match = re.search(r"\{.*\}", cost_section, re.DOTALL)
    if not match:
        return empty
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return empty
    if not isinstance(data, dict):
        return empty

    def _int(value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    return {key: _int(data.get(key)) for key in _COST_KEYS}


def parse_plan_text(raw_text: str) -> dict[str, Any]:
    """
    Parse full plan text into structured dict for DB storage.

    Decodes ⏎ → \n first, then extracts each section.
    Returns dict matching the fields in Plan model.

    Args:
        raw_text: accumulated SSE text with ⏎ encoded newlines

    Returns:
        dict with keys: brief, timeline, dineout, food, instamart,
                        health, offers, cost, totalSavings (all str),
                        totalCost, dineoutCost, foodCost, instamartCost
                        (all int | None — from [COST]'s JSON, see
                        parse_cost_block)
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
    cost_data = parse_cost_block(cost)

    return {
        "brief": brief,
        "timeline": parse_timeline(timeline),
        "dineout": dineout,
        "food": food,
        "instamart": instamart,
        "health": health,
        "offers": offers,
        "cost": cost,
        "totalCost": cost_data["total"],
        "totalSavings": extract_cost(offers, r"TOTAL SAVINGS:\s*(₹[\d,]+)"),
        "dineoutCost": cost_data["dineout"],
        "foodCost": cost_data["food"],
        "instamartCost": cost_data["instamart"],
    }
