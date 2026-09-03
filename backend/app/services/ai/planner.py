"""
services/ai/planner.py — Claude streaming plan generator.

CONCEPT: The full pipeline
----------------------------
generate_plan() orchestrates 3 stages:

  Stage 1 — Parallel MCP calls + address resolution
    Orchestrator resolves addressId (Food/Instamart) and lat/lng (Dineout)
    then fires all relevant Swiggy APIs simultaneously via asyncio.gather().
    ~100-300ms depending on network.

  Stage 2 — Offer enrichment
    Live offers fetched and injected into the prompt context.
    ~50-100ms (also async).

  Stage 3 — Claude response collection
    Full response collected from Claude claude-sonnet-4-20250514 with
    all MCP context injected. Newlines encoded as ⏎ before SSE transmission
    to prevent section markers from fragmenting across SSE frames.

CONCEPT: Why we collect rather than stream token-by-token
-----------------------------------------------------------
Token-by-token streaming caused section markers like [TIMELINE] to arrive
on separate SSE frames without a "data: " prefix — they got dropped by
the frontend parser. Fix: collect the full response, encode all newlines
as ⏎, send as one SSE message. Frontend decodes ⏎ back to \n.

Trade-off: user waits ~5s then sees the complete plan at once.
The generating animation in the frontend masks this wait effectively.

CONCEPT: Anthropic Python SDK
--------------------------------
The SDK's client.messages.create() collects the full response.
For the follow-up chat (generate_followup), we use client.messages.stream()
since that's a short conversational reply where token-by-token is fine.
"""

import asyncio
import json
import logging
from typing import AsyncIterator, Any
import anthropic

from app.core.config import settings
from app.services.ai.prompts import build_system_prompt, build_user_prompt
from app.services.mcp.orchestrator import MCPOrchestrator
from app.services.offers.engine import OffersEngine

logger = logging.getLogger(__name__)

# Fields of a PlanRequest that a chat "refine" is allowed to change.
REFINABLE_FIELDS = {
    "notes",
    "budget",
    "guest_count",
    "dietary_tags",
    "alcohol_preference",
    "venue_mode",
    "health_focus",
    "start_hour",
}

# Claude model used for plan generation and follow-up chat.
PLAN_MODEL = "claude-sonnet-4-6"


# Module-level singletons — created once, reused across all requests.
# The Anthropic client maintains its own connection pool internally.
# MCPOrchestrator and OffersEngine are stateless — safe to share.
_anthropic_client: anthropic.AsyncAnthropic | None = None
_orchestrator: MCPOrchestrator | None = None
_offers_engine: OffersEngine | None = None


def _get_clients() -> tuple[anthropic.AsyncAnthropic, MCPOrchestrator, OffersEngine]:
    """
    Lazy-initialise module-level singletons.

    CONCEPT: Why lazy init here (not at module level)?
    ---------------------------------------------------
    If we initialised at module import time, any missing env var
    (e.g. ANTHROPIC_API_KEY not set yet) would crash the import.
    Lazy init defers this until the first actual plan generation request,
    giving the app time to fully load config first.
    """
    global _anthropic_client, _orchestrator, _offers_engine
    if _anthropic_client is None:
        _anthropic_client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    if _orchestrator is None:
        _orchestrator = MCPOrchestrator()
    if _offers_engine is None:
        _offers_engine = OffersEngine()
    return _anthropic_client, _orchestrator, _offers_engine


async def generate_plan(event_data: dict[str, Any]) -> AsyncIterator[str]:
    """
    Full plan generation pipeline — yields SSE-formatted text chunks.

    This is an async generator — callers iterate over it with `async for`.
    FastAPI's StreamingResponse wraps it in an HTTP stream automatically.

    Args:
        event_data: dict from PlanRequest.model_dump() containing all
                    event configuration fields including optional lat/lng
                    from device GPS for more accurate Dineout search

    Yields:
        SSE-formatted strings: "data: <encoded_plan>\n\n"
        Terminal signal:       "data: [DONE]\n\n"
        Error signal:          "data: [ERROR] <message>\n\n"

    Example usage in endpoint:
        async def event_stream():
            async for chunk in generate_plan(request.model_dump()):
                yield chunk
        return StreamingResponse(event_stream(), media_type="text/event-stream")
    """
    client, orchestrator, offers_engine = _get_clients()

    try:
        # ── Stage 1: Parallel MCP calls ──────────────────────────────────────
        # Orchestrator resolves addresses first (addressId for Food/Instamart,
        # lat/lng for Dineout), then fires all relevant APIs concurrently.
        # Device GPS coordinates are passed through if available.

        mcp_task = orchestrator.gather_context(
            location=event_data["location"],
            event_type=event_data["event_type"],
            venue_mode=event_data["venue_mode"],
            dietary_tags=event_data.get("dietary_tags", []),
            guest_count=event_data["guest_count"],
            budget=event_data["budget"],
            start_hour=event_data.get("start_hour", 20),
            health_focus=event_data.get("health_focus", 50),
            # Optional device GPS — improves Dineout search accuracy
            lat=event_data.get("lat"),
            lng=event_data.get("lng"),
            notes=event_data.get("notes"),
            alcohol_preference=event_data.get("alcohol_preference"),
            access_token=event_data.get("access_token"),
        )

        offers_task = offers_engine.get_active_offers(
            location=event_data["location"],
            budget=event_data["budget"],
        )

        # Run MCP gathering and offers fetch in parallel
        mcp_context, offers = await asyncio.gather(
            mcp_task,
            offers_task,
            return_exceptions=True,
        )

        # Handle failures gracefully
        if isinstance(mcp_context, Exception):
            yield f"data: [ERROR] MCP fetch failed: {str(mcp_context)}\n\n"
            return
        if isinstance(offers, Exception):
            offers = []  # offers are non-critical — continue without them

        # ── Stage 2: Build prompts with full context ──────────────────────────
        # build_user_prompt handles both cases: a restaurant the user picked
        # in the Step 2 picker (passed straight through), or no pick (Claude
        # selects from the MCP data using the system-prompt rules).
        system_prompt = build_system_prompt()
        user_prompt = build_user_prompt(
            event_data=event_data,
            mcp_context=mcp_context,
            offers=offers,
            selected_dineout=event_data.get("selected_dineout"),
            selected_food=event_data.get("selected_food"),
        )

        # ── Stage 3: Collect full Claude response ─────────────────────────────
        # We use create() (not stream()) here — see module docstring for why.
        # Newlines encoded as ⏎ to prevent SSE frame fragmentation.
        message = await client.messages.create(
            model=PLAN_MODEL,
            max_tokens=2000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        full_text = message.content[0].text
        # Encode newlines as ⏎ — frontend decodes back to \n after SSE reassembly
        safe = full_text.replace("\n", "⏎")
        yield f"data: {safe}\n\n"

    except anthropic.AuthenticationError:
        yield "data: [ERROR] Invalid Anthropic API key — check ANTHROPIC_API_KEY in .env\n\n"
    except anthropic.RateLimitError:
        yield "data: [ERROR] Anthropic rate limit reached — please try again in a moment\n\n"
    except Exception as e:
        yield f"data: [ERROR] Unexpected error: {str(e)}\n\n"
    finally:
        # Always signal completion so frontend closes the stream
        yield "data: [DONE]\n\n"


async def generate_followup(
    user_message: str,
    conversation_history: list[dict],
    event_data: dict[str, Any],
    plan_text: str = "",
) -> AsyncIterator[str]:
    """
    Handle follow-up chat messages after the initial plan is generated.

    CONCEPT: Multi-turn conversation, grounded in the plan
    ------------------------------------------------------
    After the plan is shown the user asks things like "make it more
    romantic", "switch to Italian", "we added a vegan guest". These only
    make sense relative to THIS plan, so the full generated plan text is
    injected into the system prompt — without it, Claude has no idea
    "Italian" means cuisine and gives nonsense ("I only respond in English").

    The chat is advisory: it cannot mutate the saved plan. It gives concrete
    tweaks using the restaurants/items already in the plan, and for
    structural changes points the user back to the form.

    Args:
        user_message: the follow-up question or instruction
        conversation_history: previous {role, content} dicts from this session
        event_data: original event config (event_type, location, …)
        plan_text: the generated plan, newlines decoded — the grounding

    Yields:
        SSE frames. Newlines in a chunk are encoded as ⏎ (the frontend
        decodes them) so they never fragment the SSE framing.
        Terminates with "data: [DONE]\n\n".
    """
    client, _, _ = _get_clients()

    event_type = str(event_data.get("event_type", "event")).split(".")[-1]
    location = event_data.get("location") or "the user's city"
    plan_block = plan_text.strip() or "(plan text unavailable — use the conversation so far)"

    system = f"""You are Soirée, a warm, precise life-events concierge. The user already has the plan below for a {event_type} in {location} and is asking follow-up questions about it.

THE CURRENT PLAN
────────────────
{plan_block}
────────────────

How to respond:
- Every message is about THIS plan. "Switch to Italian" = Italian cuisine; "more romantic" = adjust this evening's vibe; "we added a vegan guest" = adapt the food. Never read these as language or unrelated requests.
- You cannot edit the plan yourself. For small tweaks (a dish swap, a timing change, what to ask the restaurant) give specific advice using the restaurants and items already in the plan above.
- For structural changes (different cuisine, budget, city, guests, dietary needs) say in one line what you'd change, then tell the user to update those fields in the form on the left and generate a new plan.
- Never invent restaurant names, dishes or prices — only what's in the plan above.
- Under 120 words, plain and friendly."""

    messages = conversation_history + [{"role": "user", "content": user_message}]

    try:
        async with client.messages.stream(
            model=PLAN_MODEL,
            max_tokens=400,
            system=system,
            messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                yield f"data: {text.replace(chr(10), '⏎')}\n\n"
        yield "data: [DONE]\n\n"
    except Exception as e:
        yield f"data: [ERROR] {str(e)}\n\n"


_REFINE_SYSTEM = """You refine an already-generated event plan for Soirée, a life-events concierge.

You get: the current event config (JSON), the generated plan, and the user's message.

Decide what the user wants:

  "modify" — they want the plan CHANGED (different cuisine, budget, guest
             count, dietary need, venue mode, timing, vibe). Return a
             `patch` with ONLY the fields that change, and a one-line
             `reply` confirming what you're changing.
  "answer" — they're asking a question or want advice, not a change.
             Return `reply` with a concrete, specific answer that
             references the actual restaurants / dishes / prices / items in
             the plan. No generic tips. No "you could ask them to…" filler.

PATCH RULES (only for "modify"):
- `notes` (string): return the COMPLETE notes — keep anything already
  there and add the new requirement (e.g. cuisine "italian", "make it more
  romantic", "one guest is vegan").
- `budget` (int, 100-50000), `guest_count` (int, 1-100),
  `health_focus` (int, 0-100), `start_hour` (number, 10-23).
- `dietary_tags` (list of strings): the full updated list.
- `alcohol_preference`: "yes" | "no" | "any".
- `venue_mode`: "out" | "home" | "hybrid".
- Cuisine and "more romantic" style changes go in `notes` — there is no
  cuisine field. A vegan guest → bump `guest_count`, add "Vegan" to
  `dietary_tags`, and note it.

Respond with ONLY a JSON object, no markdown fence:
{"action": "modify"|"answer", "reply": "...", "patch": { ... }}
For "answer", omit `patch` or make it {}."""

_REFINE_FALLBACK = (
    "I couldn't work out that change automatically — try rephrasing, or "
    "adjust the form on the left and generate a new plan."
)


async def refine_plan(
    user_message: str,
    conversation_history: list[dict],
    event_data: dict[str, Any],
    plan_text: str = "",
) -> dict[str, Any]:
    """
    Classify a follow-up message as a plan CHANGE or a QUESTION.

    Returns:
        {
          "action": "modify" | "answer",
          "reply": str,                     # confirmation or answer
          "patch": dict,                    # {} for answers; PlanRequest
                                            # field overrides for modifies,
                                            # already filtered to REFINABLE_FIELDS
        }

    The caller (endpoint) applies `patch` to the stored request and re-runs
    generation; the frontend swaps in the new plan. Falls back to an
    "answer" with the raw text if the model doesn't return clean JSON.
    """
    client, _, _ = _get_clients()

    context = {
        k: event_data.get(k)
        for k in ("event_type", "venue_mode", "location", "budget",
                  "guest_count", "dietary_tags", "alcohol_preference",
                  "health_focus", "start_hour", "notes")
    }
    user_block = (
        f"CURRENT CONFIG:\n{json.dumps(context, default=str, ensure_ascii=False)}\n\n"
        f"CURRENT PLAN:\n{plan_text.strip() or '(unavailable)'}\n\n"
        f"USER MESSAGE:\n{user_message}"
    )
    messages = conversation_history + [{"role": "user", "content": user_block}]

    try:
        message = await client.messages.create(
            model=PLAN_MODEL,
            max_tokens=500,
            system=_REFINE_SYSTEM,
            messages=messages,
        )
        raw = message.content[0].text.strip()
        # tolerate a ```json fence if the model adds one
        if raw.startswith("```"):
            raw = raw.split("```")[1].removeprefix("json").strip()
        parsed = json.loads(raw)
    except (anthropic.APIError, json.JSONDecodeError, KeyError, IndexError) as e:
        logger.warning("refine_plan parse failed: %s", e)
        return {"action": "answer", "reply": _REFINE_FALLBACK, "patch": {}}

    action = "modify" if parsed.get("action") == "modify" else "answer"
    reply = str(parsed.get("reply") or "").strip() or _REFINE_FALLBACK
    patch = parsed.get("patch") if isinstance(parsed.get("patch"), dict) else {}
    patch = _sanitize_patch(patch)

    if action == "modify" and not patch:
        action = "answer"  # nothing actionable came back
    return {"action": action, "reply": reply, "patch": patch}


# field -> (caster, lo, hi) for the numeric PlanRequest fields
_NUMERIC_BOUNDS = {
    "budget": (int, 100, 50000),
    "guest_count": (int, 1, 100),
    "health_focus": (int, 0, 100),
    "start_hour": (float, 10, 23),
}


def _sanitize_patch(patch: dict) -> dict:
    """Keep only refinable fields and clamp numbers into PlanRequest ranges."""
    out: dict[str, Any] = {}
    for key, value in patch.items():
        if key not in REFINABLE_FIELDS or value is None:
            continue
        if key in _NUMERIC_BOUNDS:
            cast, lo, hi = _NUMERIC_BOUNDS[key]
            try:
                out[key] = max(lo, min(hi, cast(value)))
            except (TypeError, ValueError):
                continue
        elif key == "alcohol_preference" and value in ("yes", "no", "any"):
            out[key] = value
        elif key == "venue_mode" and value in ("out", "home", "hybrid"):
            out[key] = value
        elif key == "dietary_tags" and isinstance(value, list):
            out[key] = [str(t) for t in value if t]
        elif key == "notes":
            out[key] = str(value)[:500]
    return out
