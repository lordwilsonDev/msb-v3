"""HTTP bridge for Make.com and other HTTP-only clients to call MCP-like tools."""
from __future__ import annotations

import json
import os
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter()

BASE_URL = os.getenv("MSB_MCP_BASE_URL", "http://127.0.0.1:8766")
REQUEST_TIMEOUT = int(os.getenv("MSB_MCP_REQUEST_TIMEOUT", "120"))
_MCP_BRIDGE_SECRET = os.getenv("MCP_BRIDGE_SECRET", "")
_OBSIDIAN_MCP_BASE = os.getenv("OBSIDIAN_MCP_BASE", "http://127.0.0.1:27123/mcp")
_OBSIDIAN_AUTH = os.getenv("OBSIDIAN_API_KEY", "")
_OBSIDIAN_SESSION_CACHE: dict[str, str] = {}


def _check_auth(request: Request):
    if not _MCP_BRIDGE_SECRET:
        return
    header = request.headers.get("x-mcp-secret")
    if header != _MCP_BRIDGE_SECRET:
        raise HTTPException(status_code=401, detail="unauthorized")


async def _obsidian_request(payload: dict[str, Any]) -> dict[str, Any]:
    """Initialize Obsidian MCP session and forward tool call."""
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as c:
        headers = {
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
            "authorization": f"Bearer {_OBSIDIAN_AUTH}",
        }
        session_id = _OBSIDIAN_SESSION_CACHE.get("obsidian")
        if session_id:
            headers["mcp-session-id"] = session_id

        init_resp = await c.post(_OBSIDIAN_MCP_BASE, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "bridge", "version": "1.0"},
            },
        }, headers=headers)

        new_sid = init_resp.headers.get("mcp-session-id")
        if new_sid:
            _OBSIDIAN_SESSION_CACHE["obsidian"] = new_sid
            headers["mcp-session-id"] = new_sid

        await c.post(_OBSIDIAN_MCP_BASE, json={
            "jsonrpc": "2.0", "method": "notifications/initialized"
        }, headers=headers)

        payload["id"] = payload.get("id", 2)
        r = await c.post(_OBSIDIAN_MCP_BASE, json=payload, headers=headers)
        r.raise_for_status()

        for line in r.text.splitlines():
            if line.startswith("data: "):
                data = json.loads(line[6:])
                if "result" in data:
                    return data["result"]
                if "error" in data:
                    raise HTTPException(status_code=500, detail=data["error"].get("message", "obsidian error"))
        raise HTTPException(status_code=502, detail="no data in obsidian response")


class ToolCall(BaseModel):
    tool: str = Field(..., description="MCP tool name")
    args: dict[str, Any] = Field(default_factory=dict)


@router.post("/proxy")
async def mcp_proxy(call: ToolCall, request: Request) -> dict[str, Any]:
    """HTTP proxy for msb-v3 MCP tools. Make.com calls this instead of stdio MCP."""
    _check_auth(request)
    async with httpx.AsyncClient(base_url=BASE_URL, timeout=REQUEST_TIMEOUT) as client:
        try:
            match call.tool:
                case "chat":
                    r = await client.post("/chat", json=call.args)
                case "memory_recent":
                    r = await client.get(f"/memory/{call.args.get('session', 'default')}")
                case "memory_append":
                    r = await client.post(f"/memory/{call.args.get('session', 'default')}", json={
                        "role": call.args.get("role", "user"),
                        "content": call.args.get("content", ""),
                    })
                case "memory_clear":
                    r = await client.delete(f"/memory/{call.args.get('session', 'default')}")
                case "status":
                    r = await client.get("/status")
                case "metrics_json":
                    r = await client.get("/metrics/")
                case "prometheus_metrics":
                    r = await client.get("/metrics/prometheus")
                case "ralph_loop_dashboard":
                    loop_id = call.args.get("loop_id", "")
                    if not loop_id:
                        raise HTTPException(status_code=400, detail="loop_id required")
                    r = await client.get(f"/knowledge/ralph-loop/dashboard/{loop_id}")
                case "ralph_loop_run":
                    r = await client.post("/research/assistant/ralph-loop", json=call.args)
                case "vault_list":
                    result = await _obsidian_request({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "vault_list", "arguments": call.args}})
                    return {"ok": True, "tool": call.tool, "result": result}
                case "vault_read":
                    result = await _obsidian_request({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "vault_read", "arguments": call.args}})
                    return {"ok": True, "tool": call.tool, "result": result}
                case "vault_write":
                    result = await _obsidian_request({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "vault_write", "arguments": call.args}})
                    return {"ok": True, "tool": call.tool, "result": result}
                case "vault_append":
                    result = await _obsidian_request({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "vault_append", "arguments": call.args}})
                    return {"ok": True, "tool": call.tool, "result": result}
                case "vault_patch":
                    result = await _obsidian_request({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "vault_patch", "arguments": call.args}})
                    return {"ok": True, "tool": call.tool, "result": result}
                case "vault_delete":
                    result = await _obsidian_request({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "vault_delete", "arguments": call.args}})
                    return {"ok": True, "tool": call.tool, "result": result}
                case "vault_move":
                    result = await _obsidian_request({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "vault_move", "arguments": call.args}})
                    return {"ok": True, "tool": call.tool, "result": result}
                case "vault_get_document_map":
                    result = await _obsidian_request({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "vault_get_document_map", "arguments": call.args}})
                    return {"ok": True, "tool": call.tool, "result": result}
                case "active_file_get_path":
                    result = await _obsidian_request({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "active_file_get_path", "arguments": call.args}})
                    return {"ok": True, "tool": call.tool, "result": result}
                case "periodic_note_get_path":
                    result = await _obsidian_request({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "periodic_note_get_path", "arguments": call.args}})
                    return {"ok": True, "tool": call.tool, "result": result}
                case "search_query":
                    result = await _obsidian_request({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "search_query", "arguments": call.args}})
                    return {"ok": True, "tool": call.tool, "result": result}
                case "search_simple":
                    result = await _obsidian_request({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "search_simple", "arguments": call.args}})
                    return {"ok": True, "tool": call.tool, "result": result}
                case "tag_list":
                    result = await _obsidian_request({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "tag_list", "arguments": call.args}})
                    return {"ok": True, "tool": call.tool, "result": result}
                case "command_list":
                    result = await _obsidian_request({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "command_list", "arguments": call.args}})
                    return {"ok": True, "tool": call.tool, "result": result}
                case "command_execute":
                    result = await _obsidian_request({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "command_execute", "arguments": call.args}})
                    return {"ok": True, "tool": call.tool, "result": result}
                case "open_file":
                    result = await _obsidian_request({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "open_file", "arguments": call.args}})
                    return {"ok": True, "tool": call.tool, "result": result}
                case _:
                    raise HTTPException(status_code=404, detail=f"Unknown tool: {call.tool}")

            r.raise_for_status()
            text = r.text
            try:
                return {"ok": True, "tool": call.tool, "result": r.json()}
            except Exception:
                return {"ok": True, "tool": call.tool, "result": text}

        except httpx.HTTPStatusError as e:
            raise HTTPException(status_code=e.response.status_code, detail=e.response.text)
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"Upstream error: {e}")


@router.get("/tools")
async def list_tools() -> dict[str, Any]:
    """Return available MCP-like tools for Make.com / Claude Code discovery."""
    return {
        "tools": [
            {"name": "chat", "description": "Chat with the local model", "args": ["query", "session"]},
            {"name": "memory_recent", "description": "Recent memory messages", "args": ["session", "limit"]},
            {"name": "memory_append", "description": "Append to memory", "args": ["session", "role", "content"]},
            {"name": "memory_clear", "description": "Clear memory", "args": ["session"]},
            {"name": "status", "description": "Runtime status", "args": []},
            {"name": "metrics_json", "description": "JSON metrics summary", "args": []},
            {"name": "prometheus_metrics", "description": "Prometheus metrics text", "args": []},
            {"name": "ralph_loop_dashboard", "description": "Ralph Loop dashboard", "args": ["loop_id"]},
            {"name": "ralph_loop_run", "description": "Run Ralph Loop research mission", "args": ["goal", "max_loops", "budget_cap_usd", "slug"]},
            {"name": "vault_list", "description": "List vault directory contents", "args": ["path"]},
            {"name": "vault_read", "description": "Read a vault file", "args": ["path"]},
            {"name": "vault_write", "description": "Create/overwrite a vault file", "args": ["path", "content"]},
            {"name": "vault_append", "description": "Append to a vault file", "args": ["path", "content"]},
            {"name": "vault_patch", "description": "Patch a vault file section", "args": ["path", "operation", "target", "content"]},
            {"name": "vault_delete", "description": "Delete a vault file", "args": ["path"]},
            {"name": "vault_move", "description": "Move/rename a vault file", "args": ["from_path", "to_path"]},
            {"name": "vault_get_document_map", "description": "Get vault file structure", "args": ["path"]},
            {"name": "active_file_get_path", "description": "Get currently open Obsidian file path", "args": []},
            {"name": "periodic_note_get_path", "description": "Get current periodic note path", "args": ["period"]},
            {"name": "search_query", "description": "Search vault with JsonLogic query", "args": ["query"]},
            {"name": "search_simple", "description": "Simple vault search", "args": ["query"]},
            {"name": "tag_list", "description": "List all tags in vault", "args": []},
            {"name": "command_list", "description": "List Obsidian commands", "args": []},
            {"name": "command_execute", "description": "Execute Obsidian command", "args": ["id"]},
            {"name": "open_file", "description": "Open file in Obsidian UI", "args": ["path"]},
        ]
    }
