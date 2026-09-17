"""
services/mcp/base.py — Shared base class for all Swiggy MCP clients.

CONCEPT: One transport, three services
---------------------------------------
Food, Instamart and Dineout all speak the same protocol — JSON-RPC 2.0
over HTTP POST, Bearer-token auth, `tools/call` method. Only the URL and
the set of tool names differ. So the HTTP mechanics live here once, and
each concrete client just declares its `MCP_URL` and its mock responses.

CONCEPT: Token-based mock switching
-------------------------------------
There is no static Swiggy API key. Each request carries a per-user OAuth
access token (minted via the PKCE flow, stored in Redis). If a call has a
token we hit the real MCP server; if it doesn't we return mock data with
the exact same response shape. This is the ONLY switch — there is no
`use_mock` flag to keep in sync.

REAL MCP RESPONSE FORMAT
------------------------
Swiggy MCP returns text, not structured JSON:
    {"result": {"content": [{"type": "text", "text": "Found 10 restaurants..."}]}}
`_call_mcp` therefore returns the FULL decoded envelope (not `result["result"]`)
so the orchestrator can reach into `result.content[*].text` and parse it.

The HTTP framing of that envelope isn't consistent, though: some tools
(get_addresses) reply with a plain JSON body; others
(search_restaurants_dineout, observed in production) reply SSE-framed
("event: message\ndata: {...}\n\n") even for one single, complete
response — a server-side choice under MCP's "Streamable HTTP" transport,
not something the client controls. `_real_mcp_call` tries plain JSON
first, falls back to `_parse_sse_json` on a decode failure. Before this
fallback existed, an SSE-framed dineout response raised JSONDecodeError,
which `MCPOrchestrator`'s `asyncio.gather(..., return_exceptions=True)`
silently absorbed into a per-service error result — real search results
were being thrown away, not just failing loudly.

ERROR HANDLING (per Swiggy docs)
--------------------------------
  401 → token expired or invalid     → frontend re-runs OAuth
  419 → session revoked              → full re-auth (phone + OTP again)
  403 → scope too narrow             → re-auth with broader scope
All three raise PermissionError with a machine-readable code so the
endpoint layer can return a structured re-auth signal to the frontend.

The `Accept: application/json, text/event-stream` header is REQUIRED —
Swiggy MCP returns 406 without it.
"""

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Seconds to wait on any single MCP HTTP call before giving up.
MCP_TIMEOUT_SECONDS = 10.0


def _parse_sse_json(text: str) -> dict[str, Any] | None:
    """
    Parse a Server-Sent Events body down to its JSON-RPC payload.

    MCP's "Streamable HTTP" transport lets a server answer a POST with
    either a plain JSON body or an SSE stream (Content-Type: text/
    event-stream) — observed in production against real Swiggy MCP:
    some tools (get_addresses) come back as plain JSON, others
    (search_restaurants_dineout) come back SSE-framed
    ("event: message\\ndata: {...}\\n\\n") even for one single, complete
    response. `response.json()` raises on that shape — this is the
    fallback. Returns the last complete JSON object found across all
    `data:` lines (multiple `data:` lines within one event are joined
    with \\n, per the SSE spec), or None if nothing parses.
    """
    last: dict[str, Any] | None = None
    for block in text.split("\n\n"):
        data_lines = [
            line[len("data:") :].lstrip(" ")
            for line in block.splitlines()
            if line.startswith("data:")
        ]
        if not data_lines:
            continue
        try:
            last = json.loads("\n".join(data_lines))
        except json.JSONDecodeError:
            continue
    return last


class BaseMCPClient:
    """
    Base class for the Food, Instamart and Dineout MCP clients.

    Subclasses MUST set `MCP_URL` and implement `_mock_dispatch`.
    They get `_call_mcp` for free.
    """

    #: Concrete Swiggy MCP endpoint — overridden by each subclass.
    MCP_URL: str = ""

    async def _call_mcp(
        self,
        tool_name: str,
        params: dict,
        access_token: str | None = None,
    ) -> dict[str, Any]:
        """
        Invoke one MCP tool.

        No token → mock dispatch. Token → real JSON-RPC call to `MCP_URL`.
        Returns the full decoded response envelope in both cases.
        """
        if not access_token:
            return await self._mock_dispatch(tool_name, params)
        return await self._real_mcp_call(tool_name, params, access_token)

    async def _real_mcp_call(
        self,
        tool_name: str,
        params: dict,
        access_token: str,
    ) -> dict[str, Any]:
        """
        Real MCP HTTP call with Bearer token.

        Raises:
            PermissionError  on 401 / 419 / 403 (re-auth signals)
            httpx.HTTPStatusError  on other non-2xx responses
            ValueError  if the JSON-RPC envelope carries an "error"
        """
        async with httpx.AsyncClient(timeout=MCP_TIMEOUT_SECONDS) as client:
            response = await client.post(
                self.MCP_URL,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                    # REQUIRED — Swiggy MCP returns 406 without this.
                    "Accept": "application/json, text/event-stream",
                },
                json={
                    "jsonrpc": "2.0",
                    "method": "tools/call",
                    "params": {"name": tool_name, "arguments": params},
                    "id": 1,
                },
            )

            if response.status_code == 401:
                raise PermissionError("SWIGGY_TOKEN_EXPIRED")
            if response.status_code == 419:
                raise PermissionError("SWIGGY_SESSION_REVOKED")
            if response.status_code == 403:
                raise PermissionError("SWIGGY_SCOPE_ERROR")

            logger.warning(
                "Swiggy MCP %s → status=%s body=%s",
                tool_name,
                response.status_code,
                response.text[:500],
                extra={"tool_name": tool_name, "status_code": response.status_code},
            )
            response.raise_for_status()

            try:
                result = response.json()
            except json.JSONDecodeError:
                result = _parse_sse_json(response.text)
                if result is None:
                    raise
            if "error" in result:
                raise ValueError(f"MCP error: {result['error']}")

            # Return the full envelope — the orchestrator parses
            # result["result"]["content"][*]["text"] itself.
            return result

    async def _mock_dispatch(self, tool_name: str, params: dict) -> dict:
        """Route a mock call to the right handler. Implemented by subclasses."""
        raise NotImplementedError
