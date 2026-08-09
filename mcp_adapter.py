#!/usr/bin/env python3
"""
mcp_adapter.py — stdio MCP server that fronts msb-v3's HTTP bridge.

msb-v3's mcp_bridge.py (src/msb_v3/api/mcp_bridge.py) exposes MCP-style tools
over plain HTTP (GET /mcp/tools, POST /mcp/proxy) for Make.com. It does NOT
speak MCP's JSON-RPC protocol. This script is the missing piece: Claude Code
spawns it as a stdio MCP server, and it translates each MCP tools/call into
an HTTP POST against the bridge. NOTE (2026-08-08): /mcp/tools is now
auth-gated like /proxy — this adapter sends x-mcp-secret on every request
when MCP_BRIDGE_SECRET is set, so nothing here changes, but bare curl
discovery of the manifest now requires the header.

Stdlib only — no dependency on the msb-v3 virtualenv/interpreter beyond
Python 3.11+, so it runs under any `python3` on PATH.

Env vars:
  MSB_BASE_URL       Base URL of the msb-v3 HTTP API (default: http://127.0.0.1:8766)
  MCP_BRIDGE_SECRET  Sent as the X-MCP-Secret header. Required as of
                      2026-08-07 — mcp_bridge.py's auth now fails closed
                      when the secret is unset, so every /mcp/proxy call
                      needs this set to the value in msb-v3's .env.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE_URL = os.environ.get("MSB_BASE_URL", "http://127.0.0.1:8766").rstrip("/")
MCP_PREFIX = f"{BASE_URL}/mcp"
SECRET = os.environ.get("MCP_BRIDGE_SECRET", "")
SERVER_NAME = "msb-v3"
SERVER_VERSION = "0.1.0"


def log(msg: str) -> None:
    # MCP stdio transport reserves stdout for protocol messages only.
    print(f"[mcp_adapter] {msg}", file=sys.stderr, flush=True)


def http_request(method: str, path: str, body: dict | None = None, timeout: int = 120) -> tuple[int, dict | str]:
    url = f"{MCP_PREFIX}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("content-type", "application/json")
    if SECRET:
        req.add_header("x-mcp-secret", SECRET)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw
    except urllib.error.URLError as e:
        return 502, f"msb-v3 unreachable at {MCP_PREFIX}: {e.reason}"


def fetch_tool_defs() -> list[dict]:
    status, payload = http_request("GET", "/tools")
    if status != 200 or not isinstance(payload, dict):
        log(f"WARNING: could not fetch /mcp/tools (status={status}): {payload!r}")
        return []
    tools = []
    for t in payload.get("tools", []):
        args = t.get("args", [])
        properties = {a: {"type": "string", "description": f"'{a}' argument"} for a in args}
        tools.append(
            {
                "name": t["name"],
                "description": t.get("description", ""),
                "inputSchema": {
                    "type": "object",
                    "properties": properties,
                    "additionalProperties": True,
                },
            }
        )
    return tools


def write_message(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def make_result(msg_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def make_error(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def handle_request(req: dict) -> dict | None:
    method = req.get("method")
    msg_id = req.get("id")
    params = req.get("params") or {}

    if method == "initialize":
        return make_result(
            msg_id,
            {
                "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            },
        )

    if method == "notifications/initialized":
        return None  # notification, no response

    if method == "ping":
        return make_result(msg_id, {})

    if method == "tools/list":
        return make_result(msg_id, {"tools": fetch_tool_defs()})

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        status, payload = http_request("POST", "/proxy", {"tool": name, "args": arguments})
        if status == 200 and isinstance(payload, dict) and payload.get("ok"):
            text = json.dumps(payload.get("result"), indent=2)
            return make_result(msg_id, {"content": [{"type": "text", "text": text}], "isError": False})
        detail = payload.get("detail") if isinstance(payload, dict) else payload
        text = f"msb-v3 tool '{name}' failed (status={status}): {detail}"
        return make_result(msg_id, {"content": [{"type": "text", "text": text}], "isError": True})

    if msg_id is None:
        return None  # unknown notification — ignore
    return make_error(msg_id, -32601, f"Method not found: {method}")


def main() -> None:
    log(f"starting, proxying to {MCP_PREFIX}")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as e:
            log(f"bad JSON from client: {e}")
            continue
        try:
            resp = handle_request(req)
        except Exception as e:
            resp = make_error(req.get("id"), -32603, f"Internal error: {e}")
        if resp is not None:
            write_message(resp)


if __name__ == "__main__":
    main()
