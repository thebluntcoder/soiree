"""
services/mcp/parse_mcp.py — normalise Swiggy MCP search responses.

The two "search restaurants" tools disagree on format (confirmed against
live output via `GET /search/_debug`):

  Food  (`search_restaurants`)          — a JSON blob inside the text field,
                                          followed by a "⚠️ widget" note:
      {"restaurants":[{"id","name","cuisines":[…],"avgRating",
        "costForTwo":"₹300 for two","areaName","distanceKm",
        "deliveryTimeMinutes","offer","imageUrl","availabilityStatus"}…],
        "total":10,"totalRestaurants":236,…}

  Dineout (`search_restaurants_dineout`) — numbered plain-text lines plus a
                                           trailing coordinates line:
      1. Dwarka Restaurant —  | 4.3★ |  | Dwarka (ID: 32544)
      …
      Search coordinates: latitude=28.492222, longitude=77.0782287 (…)

Both are normalised to the shape the mock clients emit:
  {"data": {"restaurants": [ {id,name,rating,locality,…} ], "coordinates"?,
            "totalResults", "source"}}

so `search.py` (the picker) and the planner prompt work identically for
real and mock data. `parse_restaurant_list` returns None for anything it
doesn't recognise (mock dicts, errors, empty) — callers keep the original.
"""

import json
import re
from typing import Any

_ID = re.compile(r"\(\s*ID\s*[:=]?\s*([^)]+?)\s*\)", re.IGNORECASE)
_RATING = re.compile(r"(\d(?:\.\d)?)\s*(?:★|stars?\b|/\s*5)", re.IGNORECASE)
_PRICE = re.compile(r"₹\s*([\d,]+)")
_LINE_START = re.compile(r"^\s*\d+\s*[.)]\s+")
_NAME_STOP = re.compile(r"\s*(?:—|–|\||\s{2,}|\(\s*ID)", re.IGNORECASE)
_COORDS = re.compile(
    r"latitude\s*[=:]\s*(-?\d+\.\d+).*?longitude\s*[=:]\s*(-?\d+\.\d+)",
    re.IGNORECASE | re.DOTALL,
)
_AD_SUFFIX = re.compile(r"\s*\((?:ad|sponsored|promoted)\)\s*$", re.IGNORECASE)


def mcp_text(response: Any) -> str:
    """The text payload of an MCP envelope; '' when it isn't a text envelope."""
    if not isinstance(response, dict):
        return ""
    content = response.get("result", response)
    if isinstance(content, dict):
        content = content.get("content", [])
    if not isinstance(content, list):
        return ""
    for chunk in content:
        if isinstance(chunk, dict) and chunk.get("type") == "text" and chunk.get("text"):
            return str(chunk["text"])
    return ""


# ── Food: embedded JSON ──────────────────────────────────────────────────────


def _embedded_json(text: str) -> dict | None:
    """First top-level {...} object in the text (Food packs one before its note)."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None


def _int_from(value: Any) -> int | None:
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        m = _PRICE.search(value) or re.search(r"\d[\d,]*", value)
        if m:
            return int(m.group(0).lstrip("₹").replace(",", ""))
    return None


def _food_from_json(blob: dict) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rank, r in enumerate(blob.get("restaurants", []) or []):
        if not isinstance(r, dict) or not r.get("id") or not r.get("name"):
            continue
        name = _AD_SUFFIX.sub("", str(r["name"])).strip()
        row: dict[str, Any] = {"id": str(r["id"]), "name": name, "_rank": rank}
        if r.get("avgRating"):
            row["rating"] = float(r["avgRating"])
        cuisines = r.get("cuisines")
        if isinstance(cuisines, list) and cuisines:
            row["cuisine"] = ", ".join(cuisines[:3])
        if (cost := _int_from(r.get("costForTwo"))) is not None:
            row["priceForTwo"] = cost  # mock's field name for Food
        if r.get("areaName"):
            row["locality"] = str(r["areaName"]).replace("?", " ").strip()
        if r.get("distanceKm") is not None:
            row["distanceKm"] = round(float(r["distanceKm"]), 1)
        if r.get("deliveryTimeMinutes"):
            row["deliveryTimeMinutes"] = int(r["deliveryTimeMinutes"])
        if r.get("deliveryTimeRange"):
            row["deliveryTimeRange"] = str(r["deliveryTimeRange"])
        if r.get("veg") is not None:
            row["veg"] = bool(r["veg"])
        if r.get("offer"):
            row["offers"] = [{"description": str(r["offer"])}]
        if r.get("imageUrl"):
            row["imageUrl"] = str(r["imageUrl"])
        row["availabilityStatus"] = r.get("availabilityStatus", "OPEN")
        if _AD_SUFFIX.search(str(r["name"])):
            row["sponsored"] = True
        rows.append(row)
    return rows


# ── Dineout: numbered text lines ─────────────────────────────────────────────


def _restaurant_lines(text: str) -> list[str]:
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
    chunks = [c.strip(" \t-–—·") for c in re.split(r"\|", _ID.sub("", line))]
    for chunk in reversed(chunks):
        if (
            chunk
            and chunk != name
            and not _RATING.search(chunk)
            and "km" not in chunk.lower()
            and "₹" not in chunk
            and not _LINE_START.match(chunk + " ")
        ):
            return chunk
    return ""


def _dineout_from_text(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rank, line in enumerate(_restaurant_lines(text)):
        rid, name = _ID.search(line), _name(line)
        if not (rid and name):
            continue
        row: dict[str, Any] = {
            "id": rid.group(1).strip(),
            "name": name,
            "_rank": rank,
        }
        if m := _RATING.search(line):
            rating = float(m.group(1))
            if rating > 0:  # "0★" = unrated / non-participating
                row["rating"] = rating
        if loc := _locality(line, name):
            row["locality"] = loc
        if m := _PRICE.search(line):
            row["costForTwo"] = int(m.group(1).replace(",", ""))
        rows.append(row)
    return rows


# ── Entry point ──────────────────────────────────────────────────────────────


def parse_restaurant_list(response: Any) -> dict[str, Any] | None:
    """
    Normalise a Food or Dineout search response. None if unrecognised
    (mock dict / error / empty) — the caller keeps the original.
    """
    text = mcp_text(response)
    if not text:
        return None

    blob = _embedded_json(text)
    coords = None
    if blob is not None and "restaurants" in blob:
        rows = _food_from_json(blob)
        source = "swiggy-food-json"
        total = int(blob.get("totalRestaurants") or blob.get("total") or len(rows))
        has_more = bool(blob.get("hasMore")) or total > len(rows)
    else:
        rows = _dineout_from_text(text)
        source = "swiggy-dineout-text"
        cm = _COORDS.search(text)
        if cm:
            coords = {"lat": float(cm.group(1)), "lng": float(cm.group(2))}
        mm = re.search(r"(\d+)\s+more\s+available", text, re.IGNORECASE)
        tm = re.search(r"[Ff]ound\s+(\d+)\s+restaurant", text)
        total = int(tm.group(1)) if tm else len(rows)
        has_more = bool(mm) or total > len(rows)

    if not rows:
        return None

    data: dict[str, Any] = {
        "restaurants": rows,
        "totalResults": total,
        "hasMore": has_more,
        "source": source,
    }
    if coords:
        data["coordinates"] = coords
    return {"data": data}
