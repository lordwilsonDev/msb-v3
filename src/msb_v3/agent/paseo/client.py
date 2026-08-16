"""Paseo adapter — MSB ↔ Paseo (docs/paseo-adapter-v1.md).

``PaseoMcpClient`` speaks the Paseo daemon's agent-management MCP surface
over Streamable HTTP (JSON-RPC 2.0): the daemon mounts it at ``/mcp/agents``
(``bootstrap.ts`` — POST/GET/DELETE, ``mcp-session-id`` sessions). Verified
tool names live in Paseo's ``agent/mcp-server.ts`` — no guessing here.

    initialize  -> tools/call(create_agent) -> wait_for_agent -> ...
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2025-03-26"
CLIENT_INFO = {"name": "msb-v3", "version": "0.2.3"}


class PaseoMcpError(RuntimeError):
    """Raised for JSON-RPC errors, HTTP failures, and protocol violations."""


def _parse_response_body(text: str, content_type: str = "") -> Any:
    """MCP Streamable HTTP may answer with plain JSON or SSE.

    JSON responses carry the JSON-RPC envelope directly. SSE responses carry
    it on ``data:`` lines — the final data line is the result (the earlier
    ones are progress/notifications).
    """
    if "text/event-stream" in content_type or text.lstrip().startswith(("event:", "data:")):
        payload: Any = None
        for line in text.splitlines():
            if line.startswith("data:"):
                payload = json.loads(line[5:].strip())
        if payload is None:
            raise PaseoMcpError("empty SSE response from MCP endpoint")
        return payload
    return json.loads(text)


class PaseoMcpClient:
    """Minimal MCP Streamable HTTP client for the Paseo daemon.

    Session lifecycle: ``initialize`` (grabs ``mcp-session-id``) ->
    ``notifications/initialized`` -> ``tools/call``. A 404 (expired session)
    transparently re-initializes and retries once. ``call_tool`` returns the
    tool's ``structuredContent`` when present, else parsed text content.
    """

    def __init__(
        self,
        url: str,
        *,
        timeout_s: float = 30.0,
        connect_timeout_s: float = 5.0,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.url = url
        self._timeout_s = timeout_s
        self._connect_timeout_s = connect_timeout_s
        self._client = client
        self._owns_client = client is None
        self._session_id: Optional[str] = None
        self._server_info: Dict[str, Any] = {}
        self._initialized = False
        self._next_id = 1

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self._timeout_s, connect=self._connect_timeout_s)
            )
            self._owns_client = True
        return self._client

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["mcp-session-id"] = self._session_id
        return headers

    async def initialize(self) -> Dict[str, Any]:
        """Bootstrap the MCP session; returns the server's initialize result."""
        http = await self._http()
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": "initialize",
            "params": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
        }
        self._next_id += 1
        try:
            resp = await http.post(self.url, json=payload, headers=self._headers())
        except httpx.HTTPError as exc:
            raise PaseoMcpError(f"cannot reach Paseo daemon at {self.url}: {exc.__class__.__name__}") from exc
        if resp.status_code >= 400:
            raise PaseoMcpError(f"initialize failed: HTTP {resp.status_code}: {resp.text[:200]}")
        self._session_id = resp.headers.get("mcp-session-id")
        body = _parse_response_body(resp.text, resp.headers.get("content-type", ""))
        result = body.get("result", {})
        self._server_info = result.get("serverInfo", {})
        # notifications/initialized — the handshake completes the session.
        try:
            await http.post(
                self.url,
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                headers=self._headers(),
            )
        except httpx.HTTPError:
            pass  # best-effort handshake; the session id is already established
        self._initialized = True
        return result

    async def call_tool(
        self,
        name: str,
        arguments: Dict[str, Any],
        *,
        timeout_s: Optional[float] = None,
    ) -> Any:
        """Call an MCP tool with the given arguments.

        ``timeout_s`` overrides the default for this call — required for
        ``wait_for_agent``, which blocks server-side until a permission
        request appears or the run completes.
        """
        if not self._initialized:
            await self.initialize()
        http = await self._http()
        payload = {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        self._next_id += 1
        timeout = httpx.Timeout(timeout_s or self._timeout_s, connect=self._connect_timeout_s)
        try:
            resp = await http.post(self.url, json=payload, headers=self._headers(), timeout=timeout)
        except httpx.TimeoutException as exc:
            raise PaseoMcpError(f"tools/call {name} timed out after {timeout_s or self._timeout_s}s") from exc
        except httpx.HTTPError as exc:
            raise PaseoMcpError(f"tools/call {name} failed: {exc.__class__.__name__}") from exc
        if resp.status_code == 404:
            # Session expired server-side — re-initialize and retry once.
            self._initialized = False
            self._session_id = None
            await self.initialize()
            try:
                resp = await http.post(self.url, json=payload, headers=self._headers(), timeout=timeout)
            except httpx.HTTPError as exc:
                raise PaseoMcpError(f"tools/call {name} failed on retry: {exc.__class__.__name__}") from exc
        if resp.status_code >= 400:
            raise PaseoMcpError(f"tools/call {name} failed: HTTP {resp.status_code}: {resp.text[:300]}")
        body = _parse_response_body(resp.text, resp.headers.get("content-type", ""))
        if isinstance(body, dict) and "error" in body:
            err = body["error"]
            raise PaseoMcpError(f"tools/call {name} error {err.get('code')}: {err.get('message')}")
        result = body.get("result", {}) if isinstance(body, dict) else {}
        structured = result.get("structuredContent")
        if structured is not None:
            return structured
        content = result.get("content") or []
        texts = [c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"]
        if texts:
            return {"content": "\n".join(texts)}
        return result

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
        self._client = None
        self._initialized = False
