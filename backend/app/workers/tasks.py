"""
workers/tasks.py — Background jobs.

CONCEPT: BackgroundTasks now, Celery later
---------------------------------------------
celery==5.6.3 has sat in requirements.txt since early on as a
forward-looking dependency — it's still genuinely unused. Decided not to
stand it up for this first ordering slice (Dineout book_table only):

  - railway.toml / Procfile deploy exactly one process
    (`alembic upgrade head && uvicorn ...`) — no worker service exists.
    Standing up Celery for real means a second Railway service, its own
    broker wiring, its own restart/monitoring story — genuine new
    infrastructure, not just code.
  - This is one bounded operation (book_table + a few-attempt retry loop,
    worst case ~15-20s), not multi-service orchestration. Celery's value
    (durable queue, cross-restart retries, distributed workers) doesn't
    pay for itself yet.
  - FastAPI's BackgroundTasks is the idiomatic minimal version of exactly
    what's needed here, zero new dependencies.

Trade-off accepted: a BackgroundTasks job is lost if the process restarts
mid-task (e.g. a deploy lands during the retry loop). place_dineout_booking
always wraps its body in try/except and writes a terminal status
(confirmed/failed) — if the process dies before that write, the plan is
left in `ordering` with no update. Visible and bounded, not silent —
acceptable for a first slice.

Revisit Celery once Food+Instamart also need background execution — three
MCP writes in parallel with independent retry/backoff is a much better
fit for Celery's primitives (chord/group) than one call is.
"""

import json
import logging
import time

from app.core.database import AsyncSessionLocal
from app.models.plan import PlanStatus
from app.services.mcp.orchestrator import MCPOrchestrator
from app.services.mcp.parse_mcp import closest_slot, parse_available_slots
from app.services.orders.dineout_ordering import book_with_retry
from app.services.plan_service import get_plan, save_order_result
from app.services import analytics

logger = logging.getLogger(__name__)


async def place_dineout_booking(plan_id: str, access_token: str | None) -> None:
    """
    Books the Dineout table for an approved, Dineout-only order request.

    Runs via FastAPI BackgroundTasks — the request's own DB session is
    gone by the time this executes (BackgroundTasks runs after the
    response is sent), so it opens its own via AsyncSessionLocal. Safe to
    do so on the same event loop the request ran on (unlike, say, a
    throwaway thread with its own loop — BackgroundTasks isn't that).

    Always terminates the plan in either 'confirmed' or 'failed' — never
    leaves it silently stuck in 'ordering' on an expected failure path.
    An unexpected exception is the one case that can still leave a plan
    stuck (if the process dies mid-task); see module docstring.
    """
    started_at = time.perf_counter()
    async with AsyncSessionLocal() as session:
        plan = await get_plan(session, plan_id)
        if not plan or not plan.dineout_selection:
            logger.error(
                "place_dineout_booking: plan or dineout_selection missing",
                extra={"plan_id": plan_id},
            )
            if plan:
                await save_order_result(
                    session, plan_id, status=PlanStatus.failed, order_error="INTERNAL_ERROR"
                )
            return

        # No access_token means mock mode, same as every other MCP call in
        # this app (BaseMCPClient._call_mcp routes on exactly this) — NOT
        # an error condition. An actually-expired real token surfaces as a
        # PermissionError from book_table itself, already handled by
        # book_with_retry below; don't preempt that with a token check here.
        selection = json.loads(plan.dineout_selection)

        try:
            orchestrator = MCPOrchestrator()
            slots_raw = await orchestrator.dineout.get_available_slots(
                selection["restaurant_id"],
                date=selection["date"],
                guest_count=selection["guest_count"],
                access_token=access_token,
            )
            live_slots = parse_available_slots(slots_raw) or selection.get("available_slots") or []
            target = closest_slot(live_slots, selection["start_hour"])
            if not target or not target.get("slotId"):
                await save_order_result(
                    session, plan_id, status=PlanStatus.failed, order_error="SLOT_UNAVAILABLE"
                )
                analytics.capture(plan.user_id, "order_failed", {
                    "plan_id": plan_id, "error": "SLOT_UNAVAILABLE", "attempts": 0,
                })
                return

            outcome = await book_with_retry(
                orchestrator.dineout,
                restaurant_id=selection["restaurant_id"],
                slot_id=target["slotId"],
                guest_count=selection["guest_count"],
                booking_date=selection["date"],
                start_hour=selection["start_hour"],
                access_token=access_token,
            )
        except Exception as e:  # noqa: BLE001 — never leave a plan stuck in 'ordering'
            logger.error(
                "place_dineout_booking: unexpected error",
                extra={"plan_id": plan_id, "restaurant_id": selection.get("restaurant_id")},
                exc_info=e,
            )
            await save_order_result(
                session, plan_id, status=PlanStatus.failed, order_error="INTERNAL_ERROR"
            )
            analytics.capture(plan.user_id, "order_failed", {
                "plan_id": plan_id, "error": "INTERNAL_ERROR", "attempts": 0,
            })
            return

        latency_ms = round((time.perf_counter() - started_at) * 1000)
        if outcome.success:
            await save_order_result(
                session, plan_id, status=PlanStatus.confirmed,
                dineout_booking_id=outcome.booking_id,
            )
            analytics.capture(plan.user_id, "order_confirmed", {
                "plan_id": plan_id, "restaurant_id": selection["restaurant_id"],
                "latency_ms": latency_ms,
            })
            logger.info(
                "Dineout booking confirmed",
                extra={
                    "plan_id": plan_id, "restaurant_id": selection["restaurant_id"],
                    "booking_id": outcome.booking_id,
                },
            )
        else:
            await save_order_result(
                session, plan_id, status=PlanStatus.failed, order_error=outcome.error
            )
            analytics.capture(plan.user_id, "order_failed", {
                "plan_id": plan_id, "error": outcome.error, "latency_ms": latency_ms,
            })
            logger.warning(
                "Dineout booking failed",
                extra={
                    "plan_id": plan_id, "restaurant_id": selection["restaurant_id"],
                    "error": outcome.error, "ambiguous": outcome.ambiguous,
                },
            )
