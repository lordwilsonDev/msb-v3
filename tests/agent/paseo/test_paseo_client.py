"""Paseo MCP client tests — framing, session handshake, error mapping.

The client is the only layer that knows the wire format; everything above it
(adapter, provider) talks verified tool names and gets back plain dicts.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from msb_v3.agent.paseo.client import (
    PaseoMcpClient,
    PaseoMcpError,
    _parse_response_body,
)


def test_initialize_and_call_tool(daemon, http_client):
    async def run():
        client = http_client()
        info = await client.initialize()
        assert info["serverInfo"]["name"] == "paseo"
        result = await client.call_tool("list_agents", {})
        assert result == {"agents": []}
        await client.close()

    asyncio.run(run())
    assert ("list_agents", {}) in daemon.tool_calls


def test_session_header_sent_on_tool_calls(daemon, http_client):
    captured: list[str] = []

    async def wrapping_handler(request: httpx.Request) -> httpx.Response:
        captured.append(request.headers.get("mcp-session-id", ""))
        return await daemon.handler(request)

    async def run():
        client = http_client(wrapping_handler)
        await client.initialize()
        await client.call_tool("list_agents", {})
        await client.close()

    asyncio.run(run())
    # initialize has no session yet; tools/call must carry it.
    assert captured[-1] == "sess-1"


def test_session_expiry_reinitializes_and_retries(daemon, http_client):
    daemon.fail_next_call = 1  # first tools/call answers 404 (expired session)

    async def run():
        client = http_client()
        result = await client.call_tool("list_agents", {})
        assert result == {"agents": []}
        await client.close()

    asyncio.run(run())


def test_jsonrpc_error_raises(daemon, http_client):
    daemon.error_next = {"code": -32002, "message": "agent not found"}

    async def run():
        client = http_client()
        with pytest.raises(PaseoMcpError, match="agent not found"):
            await client.call_tool("get_agent_status", {"agentId": "nope"})
        await client.close()

    asyncio.run(run())


def test_http_error_raises(daemon, http_client):
    async def run():
        client = PaseoMcpClient(
            "http://fake/mcp/agents",
            client=httpx.AsyncClient(
                transport=httpx.MockTransport(lambda req: httpx.Response(500, text="boom"))
            ),
        )
        with pytest.raises(PaseoMcpError, match="HTTP 500"):
            await client.call_tool("list_agents", {})
        await client.close()

    asyncio.run(run())


def test_unreachable_daemon_raises(http_client):
    async def run():
        client = PaseoMcpClient(
            "http://fake/mcp/agents",
            connect_timeout_s=0.1,
            client=httpx.AsyncClient(
                transport=httpx.MockTransport(lambda req: (_ for _ in ()).throw(httpx.ConnectError("refused")))
            ),
        )
        with pytest.raises(PaseoMcpError, match="cannot reach Paseo daemon"):
            await client.initialize()
        await client.close()

    asyncio.run(run())


def test_parse_response_body_json_and_sse():
    json_body = '{"jsonrpc":"2.0","id":1,"result":{"ok":true}}'
    assert _parse_response_body(json_body, "application/json")["result"]["ok"] is True
    sse_body = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":false}}\n\nevent: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"ok":true}}\n'
    parsed = _parse_response_body(sse_body, "text/event-stream")
    assert parsed["result"]["ok"] is True  # last data line wins
