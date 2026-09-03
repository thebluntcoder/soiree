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

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Seconds to wait on any single MCP HTTP call before giving up.
MCP_TIMEOUT_SECONDS = 10.0


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
            )
            response.raise_for_status()

            result = response.json()
            if "error" in result:
                raise ValueError(f"MCP error: {result['error']}")

            # Return the full envelope — the orchestrator parses
            # result["result"]["content"][*]["text"] itself.
            return result

    async def _mock_dispatch(self, tool_name: str, params: dict) -> dict:
        """Route a mock call to the right handler. Implemented by subclasses."""
        raise NotImplementedError
