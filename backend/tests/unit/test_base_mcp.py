"""
tests/unit/test_base_mcp.py — BaseMCPClient's real-call transport, in
particular the SSE-response fallback.

Regression coverage for a real production bug: some Swiggy MCP tools
(search_restaurants_dineout, observed live) reply SSE-framed
("event: message\ndata: {...}\n\n") even for one single, complete
response, under MCP's "Streamable HTTP" transport — a server-side choice,
not something the client requests. response.json() raised JSONDecodeError
on that shape, which MCPOrchestrator's asyncio.gather(return_exceptions=
True) silently absorbed into a per-service error result: real dineout
search results were being thrown away, not failing loudly. See
base.py's module docstring and _parse_sse_json for the fix.
"""

import httpx
import pytest
import respx

from app.services.mcp.base import BaseMCPClient, _parse_sse_json


class _DummyClient(BaseMCPClient):
    MCP_URL = "https://mcp.swiggy.com/dummy"


ENVELOPE = {
    "jsonrpc": "2.0",
    "id": 1,
    "result": {"content": [{"type": "text", "text": "Found 3 restaurants..."}]},
}


class TestParseSseJson:
    def test_single_event_single_data_line(self):
        import json

        body = f"event: message\ndata: {json.dumps(ENVELOPE)}\n\n"
        assert _parse_sse_json(body) == ENVELOPE

    def test_multiple_data_lines_joined_per_sse_spec(self):
        # SSE allows a "data:" value to span multiple lines within one
        # event, joined with \n before parsing.
        body = 'event: message\ndata: {"a":\ndata: 1}\n\n'
        assert _parse_sse_json(body) == {"a": 1}

    def test_multiple_events_returns_the_last_parseable_one(self):
        body = 'data: {"a": 1}\n\ndata: {"a": 2}\n\n'
        assert _parse_sse_json(body) == {"a": 2}

    def test_no_data_lines_is_none(self):
        assert _parse_sse_json("event: ping\n\n") is None
        assert _parse_sse_json("") is None

    def test_unparseable_data_is_none(self):
        assert _parse_sse_json("data: not json at all\n\n") is None


class TestRealMcpCallSseFallback:
    @respx.mock
    @pytest.mark.asyncio
    async def test_sse_framed_body_is_parsed(self):
        import json

        respx.post(_DummyClient.MCP_URL).mock(
            return_value=httpx.Response(
                200,
                content=f"event: message\ndata: {json.dumps(ENVELOPE)}\n\n".encode(),
                headers={"content-type": "text/event-stream"},
            )
        )
        client = _DummyClient()
        result = await client._real_mcp_call("search_x", {}, access_token="tok")
        assert result == ENVELOPE

    @respx.mock
    @pytest.mark.asyncio
    async def test_plain_json_body_still_works(self):
        """The common case, unaffected by the SSE fallback existing."""
        respx.post(_DummyClient.MCP_URL).mock(
            return_value=httpx.Response(200, json=ENVELOPE)
        )
        client = _DummyClient()
        result = await client._real_mcp_call("search_x", {}, access_token="tok")
        assert result == ENVELOPE

    @respx.mock
    @pytest.mark.asyncio
    async def test_genuinely_malformed_body_still_raises(self):
        """Neither valid JSON nor valid SSE — fails loudly, not silently."""
        respx.post(_DummyClient.MCP_URL).mock(
            return_value=httpx.Response(200, content=b"<html>not this either</html>")
        )
        client = _DummyClient()
        with pytest.raises(Exception):
            await client._real_mcp_call("search_x", {}, access_token="tok")

    @respx.mock
    @pytest.mark.asyncio
    async def test_sse_framed_rpc_error_still_raises_value_error(self):
        import json

        error_envelope = {"jsonrpc": "2.0", "id": 1, "error": {"message": "bad request"}}
        respx.post(_DummyClient.MCP_URL).mock(
            return_value=httpx.Response(
                200, content=f"data: {json.dumps(error_envelope)}\n\n".encode()
            )
        )
        client = _DummyClient()
        with pytest.raises(ValueError, match="MCP error"):
            await client._real_mcp_call("search_x", {}, access_token="tok")
