"""
services/mcp/base.py — Base MCP client with OAuth token injection.

CONCEPT: Token-based mock switching
-------------------------------------
Previously: use_mock = not bool(SWIGGY_API_KEY)  <- wrong, no static API key
Now:        use_mock = not bool(access_token)     <- correct, per-user OAuth token

The token comes from the user's session stored in Redis after OAuth.
Each MCP call receives the token as a parameter — clients are stateless.

ERROR HANDLING per Swiggy docs:
  401 → token expired or invalid → frontend re-runs OAuth
  419 → session revoked → full re-auth (phone + OTP again)
  403 → scope too narrow → re-auth with broader scope
"""

import httpx
from typing import Any


class BaseMCPClient:
    """
    Base class for all Swiggy MCP clients.
    Handles Bearer token injection and 401/419 error signalling.
    """

    MCP_URL: str = ""

    async def _call_mcp(
        self,
        tool_name: str,
        params: dict,
        access_token: str | None = None,
    ) -> dict[str, Any]:
        """
        Core MCP tool invocation with token injection.
        Routes to mock if no access_token, real MCP if token provided.
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
        Swiggy MCP uses JSON-RPC over HTTP POST.

        Raises PermissionError on 401/419/403 so the endpoint
        can return a structured re-auth signal to the frontend.
        """
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                self.MCP_URL,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
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

            response.raise_for_status()
            result = response.json()

            if "error" in result:
                raise ValueError(f"MCP error: {result['error']}")

            return result.get("result", {})

    async def _mock_dispatch(self, tool_name: str, params: dict) -> dict:
        raise NotImplementedError
