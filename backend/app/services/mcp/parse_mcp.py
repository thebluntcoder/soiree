"""
services/mcp/parse_mcp.py — turn Swiggy MCP *text* responses into structured data.

Real Swiggy MCP replies are human-readable text, not JSON:

  {"result": {"content": [{"type": "text", "text":
    'Found 39 restaurant(s) matching "restaurant", showing 10. '
    '29 more available, call again with offset=10.\n'
    '1. Farzi Cafe — | 4.6★ | | Hazratganj (ID: 824088)\n'
    '2. Barkaas Indo Arabic Restaurant — | 4.8★ | | Hazratganj (ID: 824099)\n'
    '...'}]}}

This module parses that into the SAME `{"data": {"restaurants": [...]}}`
shape the mock clients emit, so the restaurant picker (search.py) and the
planner prompt behave identically for real and mock data. Before this,
the picker only worked in mock mode — a real token gave an unparsed
envelope that `search.py` read as an empty list.

⚠️ FORMAT: the regexes below are built from the documented format and the
`GET /search/_debug` samples. They are deliberately lenient — a line that
doesn't parse is skipped, and if nothing parses the caller keeps the raw
text (Claude still reads it in the plan prompt). Tune against live output;
see TODO.md §2.
"""

import re
from typing import Any

_ID = re.compile(r"\(\s*ID\s*[:=]?\s*([^)]+?)\s*\)", re.IGNORECASE)
_RATING = re.compile(r"([0-5](?:\.\d)?)\s*(?:★|stars?\b|/\s*5)", re.IGNORECASE)
_PRICE = re.compile(r"₹\s*([\d,]+)")
_KM = re.compile(r"([\d.]+)\s*k(?:ms?|ilomet)", re.IGNORECASE)
_MINS = re.compile(r"(\d+)\s*(?:min|minute)", re.IGNORECASE)
_LINE_START = re.compile(r"^\s*\d+\s*[.)]\s+")
_NAME_STOP = re.compile(r"\s*(?:[—–|]|\s{2,}|\(\s*ID)", re.IGNORECASE)


def mcp_text(response: Any) -> str:
    """The text payload of an MCP envelope; '' when it isn't a text envelope."""
    if not isinstance(response, dict):
        return ""
    content = response.get("result", {})
    if isinstance(content, dict):
        content = content.get("content", [])
    if not isinstance(content, list):
        return ""
    for chunk in content:
        if isinstance(chunk, dict) and chunk.get("type") == "text" and chunk.get("text"):
            return str(chunk["text"])
    return ""


def _restaurant_lines(text: str) -> list[str]:
    """Numbered lines that carry an (ID: …) — the actual restaurant rows."""
    return [
        ln.strip()
        for ln in text.splitlines()
        if _LINE_START.match(ln) and _ID.search(ln)
    ]


def _name(line: str) -> str:
    body = _LINE_START.sub("", _ID.sub("", line))
    stop = _NAME_STOP.search(body)
    name = body[: stop.start()] if stop else body
    return name.strip(" \t-–—|·,")


def _locality(line: str, name: str) -> str:
    """Best guess at the area/locality — a trailing chunk that isn't a metric."""
    chunks = [c.strip(" \t-–—·") for c in re.split(r"[|]", _ID.sub("", line))]
    for chunk in reversed(chunks):
        if (
            chunk
            and chunk != name
            and not _RATING.search(chunk)
            and not _KM.search(chunk)
            and "₹" not in chunk
            and not _LINE_START.match(chunk + " ")
        ):
            return chunk
    return ""


def _parse_lines(text: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rank, line in enumerate(_restaurant_lines(text)):
        rid = _ID.search(line)
        name = _name(line)
        if not (rid and name):
            continue
        row: dict[str, Any] = {
            "id": rid.group(1).strip(),
            "name": name,
            "_rank": rank,  # original result order — a weak tie-breaker
        }
        if m := _RATING.search(line):
            row["rating"] = float(m.group(1))
        if m := _KM.search(line):
            row["distanceKm"] = float(m.group(1))
        if m := _MINS.search(line):
            row["deliveryTimeMinutes"] = int(m.group(1))
        if m := _PRICE.search(line):
            row["costForTwo"] = int(m.group(1).replace(",", ""))
        if loc := _locality(line, name):
            row["locality"] = loc
        out.append(row)
    return out


def parse_restaurant_list(response: Any) -> dict[str, Any] | None:
    """
    Parse a search_restaurants / search_restaurants_dineout text response.

    Returns `{"data": {"restaurants": [...], "totalResults": n,
    "source": "swiggy-mcp-text"}}` or None if it isn't parseable text
    (mock responses, errors, empty) — callers keep the original in that case.
    """
    text = mcp_text(response)
    if not text:
        return None
    rows = _parse_lines(text)
    if not rows:
        return None
    return {
        "data": {
            "restaurants": rows,
            "totalResults": len(rows),
            "source": "swiggy-mcp-text",
        }
    }
