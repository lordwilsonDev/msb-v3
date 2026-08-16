"""Fake Paseo daemon — an in-process stand-in for the daemon's /mcp/agents
MCP endpoint (Streamable HTTP, JSON-RPC). Speaks the verified tool names
from Paseo's agent/mcp-server.ts so adapter tests exercise real framing
without a daemon."""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List, Optional

import httpx
import pytest


class FakePaseoDaemon:
    def __init__(self) -> None:
        self.agents: Dict[str, Dict[str, Any]] = {}
        self.wait_queue: Dict[str, List[Dict[str, Any]]] = {}
        self.responded: List[tuple] = []  # (agent_id, request_id, response)
        self.cancelled: List[str] = []
        self.tool_calls: List[tuple] = []  # (name, arguments)
        self.fail_next_call = 0  # answer this many tools/call with 404 (expired session)
        self.error_next: Optional[Dict[str, Any]] = None  # JSON-RPC error envelope
        self.always_running = False  # wait_for_agent never completes (orphan test)
        self.activity_queue: Dict[str, List[Dict[str, Any]]] = {}  # agent -> activity responses
        self.activity_fail = False  # get_agent_activity fails (best-effort sampling)
        self.wait_block_s = 0.0  # wait_for_agent blocks this long (lets sampling interleave)
        self._seq = 0

    def _next_agent_id(self) -> str:
        self._seq += 1
        return f"agent-{self._seq}"

    async def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        method = body.get("method")
        rid = body.get("id")
        if method == "initialize":
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "serverInfo": {"name": "paseo", "version": "test"},
                    },
                },
                headers={"mcp-session-id": "sess-1"},
            )
        if method == "notifications/initialized":
            return httpx.Response(202, content=b"")
        if method == "tools/call":
            if self.fail_next_call > 0:
                self.fail_next_call -= 1
                return httpx.Response(
                    404,
                    json={
                        "jsonrpc": "2.0",
                        "id": rid,
                        "error": {"code": -32001, "message": "session not found"},
                    },
                )
            if self.error_next is not None:
                err = self.error_next
                self.error_next = None
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": rid, "error": err})
            params = body.get("params", {})
            name = params.get("name", "")
            args = params.get("arguments", {})
            self.tool_calls.append((name, args))
            if name == "wait_for_agent" and self.wait_block_s > 0:
                await asyncio.sleep(self.wait_block_s)
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {"content": [], "structuredContent": self._dispatch(name, args)},
                },
            )
        return httpx.Response(
            500,
            json={"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "method not found"}},
        )

    def _dispatch(self, name: str, args: Dict[str, Any]) -> Any:
        if name == "create_agent":
            agent_id = self._next_agent_id()
            self.agents[agent_id] = {"args": args, "lifecycle": "idle", "lastMessage": None, "pending": []}
            return {
                "agentId": agent_id,
                "type": args.get("provider"),
                "status": "idle",
                "cwd": args.get("cwd"),
                "currentModeId": None,
                "availableModes": [],
                "lastMessage": None,
                "permission": None,
            }
        if name == "wait_for_agent":
            agent_id = args["agentId"]
            if self.always_running:
                return {"agentId": agent_id, "status": "running", "permission": None, "lastMessage": None}
            queue = self.wait_queue.get(agent_id)
            item = queue.pop(0) if queue else None
            if item is None:
                agent = self.agents.get(agent_id, {})
                item = {"status": "idle", "permission": None, "lastMessage": agent.get("lastMessage")}
            if item.get("permission"):
                agent = self.agents.setdefault(agent_id, {})
                agent["pending"] = [item["permission"]]
            return {
                "agentId": agent_id,
                "status": item["status"],
                "permission": item.get("permission"),
                "lastMessage": item.get("lastMessage"),
            }
        if name == "send_agent_prompt":
            agent = self.agents.setdefault(args["agentId"], {})
            agent["lastMessage"] = args.get("prompt")
            return {"success": True, "status": "idle", "permission": None, "lastMessage": None}
        if name == "respond_to_permission":
            self.responded.append((args["agentId"], args["requestId"], args["response"]))
            agent = self.agents.setdefault(args["agentId"], {})
            if args["response"].get("behavior") == "allow":
                agent["pending"] = []
            return {"success": True}
        if name == "get_agent_status":
            agent = self.agents.get(args["agentId"], {})
            return {
                "status": agent.get("lifecycle", "idle"),
                "snapshot": {
                    "lifecycle": agent.get("lifecycle", "idle"),
                    "cwd": (agent.get("args") or {}).get("cwd"),
                    "currentModeId": None,
                    "lastMessage": agent.get("lastMessage"),
                    "pendingPermissions": agent.get("pending", []),
                },
            }
        if name == "get_agent_activity":
            if self.activity_fail:
                raise httpx.ConnectError("activity unavailable")
            queue = self.activity_queue.get(args["agentId"])
            item = queue.pop(0) if queue else {"updateCount": 0, "currentModeId": None, "content": ""}
            return {"agentId": args["agentId"], **item}
        if name in ("cancel_agent", "kill_agent"):
            self.cancelled.append(args["agentId"])
            return {"success": True}
        if name == "list_agents":
            return {"agents": []}
        raise AssertionError(f"unexpected tool call: {name}")


@pytest.fixture()
def daemon() -> FakePaseoDaemon:
    return FakePaseoDaemon()


@pytest.fixture()
def http_client(daemon):
    """An httpx.AsyncClient routed at the fake daemon, plus its MCP client."""

    def _make(transport=None):
        import httpx as _httpx

        from msb_v3.agent.paseo.client import PaseoMcpClient

        return PaseoMcpClient(
            "http://fake/mcp/agents",
            client=_httpx.AsyncClient(transport=_httpx.MockTransport(transport or daemon.handler)),
        )

    return _make
