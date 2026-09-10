"""
tests/unit/test_plans_stream.py — SSE heartbeat wrapper for /plans/generate.

generate_plan() goes silent for 30-60s (MCP + a non-streaming Claude call);
Railway's edge resets an idle HTTP/2 stream in that window. `_with_heartbeat`
keeps the stream warm with `: ping` comments.
"""

import asyncio

import pytest

from app.api.v1.endpoints.plans import _with_heartbeat


async def _quick():
    yield "data: PLAN_ID:abc\n\n"
    yield "data: the plan\n\n"
    yield "data: [DONE]\n\n"


async def _slow():
    yield "data: PLAN_ID:abc\n\n"
    await asyncio.sleep(0.25)  # longer than the test interval → heartbeats
    yield "data: the plan\n\n"
    yield "data: [DONE]\n\n"


async def _raises():
    yield "data: PLAN_ID:abc\n\n"
    raise RuntimeError("mcp exploded")


@pytest.mark.asyncio
async def test_passes_chunks_through_without_gaps():
    out = [c async for c in _with_heartbeat(_quick(), interval=0.1)]
    assert out == [
        "data: PLAN_ID:abc\n\n",
        "data: the plan\n\n",
        "data: [DONE]\n\n",
    ]


@pytest.mark.asyncio
async def test_emits_heartbeats_during_idle_gap():
    out = [c async for c in _with_heartbeat(_slow(), interval=0.05)]
    assert ": ping\n\n" in out
    # real chunks still arrive, in order, around the pings
    assert [c for c in out if not c.startswith(":")] == [
        "data: PLAN_ID:abc\n\n",
        "data: the plan\n\n",
        "data: [DONE]\n\n",
    ]


@pytest.mark.asyncio
async def test_propagates_generator_error():
    with pytest.raises(RuntimeError, match="mcp exploded"):
        async for _ in _with_heartbeat(_raises(), interval=0.1):
            pass
