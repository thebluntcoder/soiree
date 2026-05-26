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
from typing import AsyncIterator, Any
import anthropic

from app.core.config import settings
from app.services.ai.prompts import build_system_prompt, build_user_prompt
from app.services.mcp.orchestrator import MCPOrchestrator
from app.services.offers.engine import OffersEngine


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
        system_prompt = build_system_prompt()
        user_prompt = build_user_prompt(
            event_data=event_data,
            mcp_context=mcp_context,
            offers=offers,
        )

        # ── Stage 3: Collect full Claude response ─────────────────────────────
        # We use create() (not stream()) here — see module docstring for why.
        # Newlines encoded as ⏎ to prevent SSE frame fragmentation.
        message = await client.messages.create(
            model="claude-sonnet-4-20250514",
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
) -> AsyncIterator[str]:
    """
    Handle follow-up chat messages after the initial plan is generated.

    CONCEPT: Multi-turn conversation
    ----------------------------------
    After the plan is shown, the user can ask follow-up questions:
    "make it more romantic", "switch to Italian", "we added a vegan guest"

    We maintain conversation_history (list of {role, content} dicts) and
    send the full history with each follow-up. Claude uses this context
    to give coherent answers without the user re-explaining everything.

    We use stream() here (unlike generate_plan) because follow-up replies
    are short conversational text — no section markers to fragment, so
    token-by-token streaming is fine and gives a better typing effect.

    Args:
        user_message: the follow-up question or instruction
        conversation_history: previous {role, content} dicts from this session
        event_data: original event config for context

    Yields:
        SSE-formatted text chunks, then "data: [DONE]\n\n"
    """
    client, _, _ = _get_clients()

    system = f"""You are Soirée, a life events concierge. You've already generated a plan for a {event_data.get("event_type", "event")} in {event_data.get("location", "the user's city")}.

Answer follow-up questions helpfully and specifically. Keep responses concise (under 150 words).
If asked to regenerate the plan, tell the user to use the regenerate button.
Never invent new restaurant names or prices — refer only to what was in the original plan."""

    messages = conversation_history + [{"role": "user", "content": user_message}]

    try:
        async with client.messages.stream(
            model="claude-sonnet-4-20250514",
            max_tokens=400,
            system=system,
            messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                yield f"data: {text}\n\n"
        yield "data: [DONE]\n\n"
    except Exception as e:
        yield f"data: [ERROR] {str(e)}\n\n"
