"""
services/orders/dineout_ordering.py — book_table retry/idempotency logic.

CONCEPT: Why this is the hard part
-------------------------------------
book_table is NOT idempotent, and Swiggy documents no idempotency-key
param to send — so a naive "retry on any error" risks a double booking,
and a naive "never retry" throws away recoverable failures for no reason.

Swiggy's own advice ("on a 5xx, call get_booking_status before retrying")
only works once you already HAVE a bookingId — but a 5xx on the very
FIRST book_table call, by definition, never gave us one. This module is
built around that gap rather than pretending it doesn't exist.

THE STATE MACHINE
--------------------
Each book_table attempt is classified into one of three buckets:

1. Success — 2xx, a bookingId comes back. Done.

2. Clean failure (nothing was committed server-side):
   - PermissionError (401/419/403) → stop immediately, never retry —
     this needs re-auth, not a retry.
   - 4xx / a JSON-RPC "error" → Swiggy rejected the request outright
     (e.g. a stale slotId). Exactly ONE corrective retry: re-fetch
     get_available_slots live, run closest_slot() against fresh data,
     retry once with a new slotId if one exists. Otherwise fail
     SLOT_UNAVAILABLE.

3. Ambiguous failure (5xx / timeout / transport error) — we genuinely
   don't know if the booking went through. Resolve in this order,
   stopping at the first definite answer:
     a. Try to recover a bookingId from the failed response body itself
        (parse_booking is defensive — 5xx bodies are often opaque
        gateway pages, this frequently finds nothing, but costs nothing
        to try).
     b. If recovered, call get_booking_status(booking_id) — this is
        where Swiggy's advice becomes literally applicable.
        CONFIRMED/PENDING → success, no retry. Anything else (e.g.
        NOT_FOUND) → safe to retry.
     c. No recoverable bookingId (the common case) — the only other
        signal available: re-fetch get_available_slots live and check
        the SAME slotId. Still available → nothing changed server-side,
        safe to retry (with backoff). Missing/unavailable, OR the
        re-check itself fails → STOP, do not retry. We cannot tell
        whether that's genuinely us or someone else booking the slot in
        the meantime, and retrying into that ambiguity risks a double
        booking. This is a heuristic, not proof.
     d. MAX_ATTEMPTS exhausted with no success → RETRY_EXHAUSTED.

No app-side idempotency key is invented and sent to Swiggy — dineout.py's
own docstring already warns unknown params error out, and there's no
documented param to send. What Soirée DOES guard, for free: its own
system never double-submits book_table for the same plan (double-click,
duplicate task) — via Plan.status as a compare-and-swap lock
(plan_service.approve_and_claim_for_ordering), not anything in this module.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.services.mcp.dineout import DineoutMCPClient
from app.services.mcp.parse_mcp import closest_slot, parse_available_slots, parse_booking

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
BACKOFF_SECONDS = [2, 5]  # between attempt 1->2, 2->3 (ambiguous-retry path only)


@dataclass
class BookingOutcome:
    success: bool
    booking_id: str | None = None
    error: str | None = None
    ambiguous: bool = False


def _extract_response_body(exc: httpx.HTTPStatusError) -> dict[str, Any] | None:
    """Best-effort JSON body from a failed response — used to try to
    recover a bookingId out of a 5xx before falling back to the slots
    heuristic. httpx reads the full body before raise_for_status(), so
    it's still attached; a non-JSON (gateway HTML) body just yields None."""
    try:
        return exc.response.json()
    except Exception:  # noqa: BLE001 — opaque error bodies are expected
        return None


async def _slot_still_available(
    dineout: DineoutMCPClient,
    restaurant_id: str,
    booking_date: str,
    guest_count: int,
    slot_id: str,
    access_token: str,
) -> bool:
    """
    Re-check one specific slotId against a fresh get_available_slots call.
    False for both "genuinely unavailable now" and "the re-check itself
    failed" — an inconclusive re-check is not a safe-to-retry signal
    either way, so callers treat both the same (stop, don't retry).
    """
    try:
        raw = await dineout.get_available_slots(
            restaurant_id, date=booking_date, guest_count=guest_count,
            access_token=access_token,
        )
        slots = parse_available_slots(raw) or []
    except Exception:  # noqa: BLE001 — a failed re-check is itself inconclusive
        return False
    return any(
        s.get("slotId") == slot_id and s.get("available", True) for s in slots
    )


async def _refresh_slot(
    dineout: DineoutMCPClient,
    restaurant_id: str,
    booking_date: str,
    guest_count: int,
    start_hour: float,
    access_token: str,
) -> str | None:
    """Re-fetch live slots and pick the one closest to the originally
    intended start_hour, for the one-shot 4xx corrective retry. None if
    nothing's bookable (fetch failed, or no slot has a slotId)."""
    try:
        raw = await dineout.get_available_slots(
            restaurant_id, date=booking_date, guest_count=guest_count,
            access_token=access_token,
        )
        slots = parse_available_slots(raw) or []
    except Exception:  # noqa: BLE001
        return None
    slot = closest_slot(slots, start_hour)
    return slot.get("slotId") if slot else None


async def _resolve_ambiguous(
    dineout: DineoutMCPClient,
    restaurant_id: str,
    booking_date: str,
    guest_count: int,
    slot_id: str,
    access_token: str,
    body: dict[str, Any] | None,
) -> BookingOutcome | None:
    """
    Runs the ambiguous-failure resolution (§3 in the module docstring).
    Returns a terminal BookingOutcome (success or a definite failure), or
    None to mean "safe to retry" — the caller owns the backoff/attempt loop.
    """
    if body is not None:
        recovered = parse_booking(body)
        if recovered and recovered.get("booking_id"):
            status_raw = await dineout.get_booking_status(
                recovered["booking_id"], access_token=access_token
            )
            status = parse_booking(status_raw)
            if status and (status.get("status") or "").upper() in ("CONFIRMED", "PENDING"):
                return BookingOutcome(success=True, booking_id=recovered["booking_id"])
            return None  # NOT_FOUND or unrecognised — safe to retry

    still_available = await _slot_still_available(
        dineout, restaurant_id, booking_date, guest_count, slot_id, access_token
    )
    if still_available:
        return None  # nothing changed server-side — safe to retry
    return BookingOutcome(success=False, error="DINEOUT_BOOKING_AMBIGUOUS", ambiguous=True)


async def book_with_retry(
    dineout: DineoutMCPClient,
    restaurant_id: str,
    slot_id: str,
    guest_count: int,
    booking_date: str,
    start_hour: float,
    access_token: str,
) -> BookingOutcome:
    """Book one table, retrying only where it's actually safe to. See the
    module docstring for the full state machine."""
    current_slot_id = slot_id
    corrected_once = False

    for attempt in range(1, MAX_ATTEMPTS + 1):
        log_extra = {
            "restaurant_id": restaurant_id, "slot_id": current_slot_id, "attempt": attempt,
        }
        try:
            raw = await dineout.book_table(
                restaurant_id, current_slot_id, guest_count, booking_date,
                access_token=access_token,
            )
        except PermissionError as e:
            logger.warning("book_table permission error — not retrying", extra=log_extra)
            return BookingOutcome(success=False, error=str(e))

        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            if 400 <= status_code < 500:
                if corrected_once:
                    logger.warning(
                        "book_table rejected twice, no slot to correct to", extra=log_extra
                    )
                    return BookingOutcome(success=False, error="SLOT_UNAVAILABLE")
                corrected_once = True
                fresh_slot = await _refresh_slot(
                    dineout, restaurant_id, booking_date, guest_count, start_hour, access_token
                )
                if fresh_slot is None:
                    return BookingOutcome(success=False, error="SLOT_UNAVAILABLE")
                logger.info(
                    "book_table 4xx — retrying once with a refreshed slot", extra=log_extra
                )
                current_slot_id = fresh_slot
                continue  # doesn't consume the backoff schedule

            logger.warning("book_table 5xx — resolving ambiguous failure", extra=log_extra)
            outcome = await _resolve_ambiguous(
                dineout, restaurant_id, booking_date, guest_count, current_slot_id,
                access_token, body=_extract_response_body(e),
            )
            if outcome is not None:
                return outcome
            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(BACKOFF_SECONDS[attempt - 1])
                continue
            return BookingOutcome(success=False, error="RETRY_EXHAUSTED")

        except httpx.TransportError:
            logger.warning("book_table transport error — resolving ambiguous failure", extra=log_extra)
            outcome = await _resolve_ambiguous(
                dineout, restaurant_id, booking_date, guest_count, current_slot_id,
                access_token, body=None,
            )
            if outcome is not None:
                return outcome
            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(BACKOFF_SECONDS[attempt - 1])
                continue
            return BookingOutcome(success=False, error="RETRY_EXHAUSTED")

        except ValueError:
            # JSON-RPC-level rejection — Swiggy rejected the request
            # outright, same as a 4xx: one corrective retry.
            if corrected_once:
                return BookingOutcome(success=False, error="SLOT_UNAVAILABLE")
            corrected_once = True
            fresh_slot = await _refresh_slot(
                dineout, restaurant_id, booking_date, guest_count, start_hour, access_token
            )
            if fresh_slot is None:
                return BookingOutcome(success=False, error="SLOT_UNAVAILABLE")
            current_slot_id = fresh_slot
            continue

        booking = parse_booking(raw)
        if booking and booking.get("booking_id"):
            return BookingOutcome(success=True, booking_id=booking["booking_id"])

        # 2xx but nothing recognisable came back — same ambiguity as a 5xx
        # with no recoverable id, resolved the same way.
        logger.warning("book_table 2xx but unparseable — resolving ambiguous failure", extra=log_extra)
        if await _slot_still_available(
            dineout, restaurant_id, booking_date, guest_count, current_slot_id, access_token
        ):
            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(BACKOFF_SECONDS[attempt - 1])
                continue
            return BookingOutcome(success=False, error="RETRY_EXHAUSTED")
        return BookingOutcome(success=False, error="DINEOUT_BOOKING_AMBIGUOUS", ambiguous=True)

    return BookingOutcome(success=False, error="RETRY_EXHAUSTED")
