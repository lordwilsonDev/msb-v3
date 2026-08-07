"""HTTP bridge for Make.com and other HTTP-only clients to call MCP-like tools."""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter()

BASE_URL = os.getenv("MSB_MCP_BASE_URL", "http://127.0.0.1:8766")
REQUEST_TIMEOUT = int(os.getenv("MSB_MCP_REQUEST_TIMEOUT", "120"))
_MCP_BRIDGE_SECRET = os.getenv("MCP_BRIDGE_SECRET", "")
_VAULT_BASE = Path("/Users/lordwilson/Documents/Vault").resolve()
_AUDIT_LOGGER = logging.getLogger("msb_v3.mcp_audit")


class _AuditEvent(BaseModel):
    actor: str
    action: str
    target: str
    timestamp: str
    result: str


def _log_audit(event: _AuditEvent) -> None:
    try:
        _AUDIT_LOGGER.info(json.dumps(event.model_dump(), ensure_ascii=False))
    except Exception:
        pass


def _client_identity(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def _check_auth(request: Request) -> None:
    header = request.headers.get("x-mcp-secret")
    if not header or header != _MCP_BRIDGE_SECRET:
        raise HTTPException(status_code=401, detail="unauthorized")


def _normalize_vault_path(raw: str) -> Path:
    if raw is None:
        raw = ""
    path = (_VAULT_BASE / raw).resolve()
    if not str(path).startswith(str(_VAULT_BASE)):
        raise HTTPException(status_code=400, detail="path traversal detected")
    return path


class ToolCall(BaseModel):
    tool: str = Field(..., description="MCP tool name")
    args: dict[str, Any] = Field(default_factory=dict)


@router.post("/proxy")
async def mcp_proxy(call: ToolCall, request: Request) -> dict[str, Any]:
    """HTTP proxy for msb-v3 MCP tools. Make.com calls this instead of stdio MCP."""
    _check_auth(request)
    actor = _client_identity(request)
    started = time.time()
    status = "success"

    try:
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
                        path = _normalize_vault_path(call.args.get("path", ""))
                        try:
                            files = [p.name + ("/" if p.is_dir() else "") for p in sorted(path.iterdir())]
                            return {"ok": True, "tool": call.tool, "result": {"files": files[:100]}}
                        except Exception as exc:
                            raise HTTPException(status_code=500, detail=f"vault_list failed: {exc}") from exc
                    case "vault_read":
                        target = _normalize_vault_path(call.args.get("path", ""))
                        if not target.exists() or not target.is_file():
                            raise HTTPException(status_code=404, detail=f"File not found: {call.args.get('path')}")
                        _log_audit(_AuditEvent(
                            actor=actor,
                            action="read_file",
                            target=str(target),
                            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
                            result="success",
                        ))
                        return {"ok": True, "tool": call.tool, "result": {"content": target.read_text(encoding="utf-8", errors="replace")}}
                    case "vault_write":
                        target = _normalize_vault_path(call.args.get("path", ""))
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text(call.args.get("content", ""), encoding="utf-8")
                        _log_audit(_AuditEvent(
                            actor=actor,
                            action="write_file",
                            target=str(target),
                            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
                            result="success",
                        ))
                        return {"ok": True, "tool": call.tool, "result": {"written": str(target)}}
                    case "vault_append":
                        target = _normalize_vault_path(call.args.get("path", ""))
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with target.open("a", encoding="utf-8") as f:
                            f.write(call.args.get("content", ""))
                        _log_audit(_AuditEvent(
                            actor=actor,
                            action="append_file",
                            target=str(target),
                            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
                            result="success",
                        ))
                        return {"ok": True, "tool": call.tool, "result": {"appended": str(target)}}
                    case "vault_patch":
                        target = _normalize_vault_path(call.args.get("path", ""))
                        if not target.exists():
                            raise HTTPException(status_code=404, detail=f"File not found: {target}")
                        content = target.read_text(encoding="utf-8", errors="replace")
                        operation = call.args.get("operation", "replace")
                        pattern = call.args.get("target", "")
                        replacement = call.args.get("content", "")
                        if operation == "replace":
                            new_content = content.replace(pattern, replacement)
                        elif operation == "regex":
                            new_content = re.sub(pattern, replacement, content, flags=re.MULTILINE)
                        else:
                            raise HTTPException(status_code=400, detail=f"Unknown operation: {operation}")
                        target.write_text(new_content, encoding="utf-8")
                        _log_audit(_AuditEvent(
                            actor=actor,
                            action="patch_file",
                            target=str(target),
                            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
                            result="success",
                        ))
                        return {"ok": True, "tool": call.tool, "result": {"patched": str(target)}}
                    case "vault_delete":
                        target = _normalize_vault_path(call.args.get("path", ""))
                        if not target.exists():
                            raise HTTPException(status_code=404, detail=f"File not found: {target}")
                        target.unlink()
                        _log_audit(_AuditEvent(
                            actor=actor,
                            action="delete_file",
                            target=str(target),
                            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
                            result="success",
                        ))
                        return {"ok": True, "tool": call.tool, "result": {"deleted": str(target)}}
                    case "vault_move":
                        src = _normalize_vault_path(call.args.get("from_path", ""))
                        dst = _normalize_vault_path(call.args.get("to_path", ""))
                        if not src.exists():
                            raise HTTPException(status_code=404, detail=f"Source not found: {src}")
                        dst.parent.mkdir(parents=True, exist_ok=True)
                        src.rename(dst)
                        _log_audit(_AuditEvent(
                            actor=actor,
                            action="move_file",
                            target=f"{src} -> {dst}",
                            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
                            result="success",
                        ))
                        return {"ok": True, "tool": call.tool, "result": {"from": str(src), "to": str(dst)}}
                    case "vault_get_document_map":
                        root = _normalize_vault_path(call.args.get("path", ""))
                        if not root.exists() or not root.is_dir():
                            raise HTTPException(status_code=404, detail=f"Directory not found: {call.args.get('path')}")
                        tree = {}
                        for p in sorted(root.rglob("*.md")):
                            rel = p.relative_to(root)
                            parts = rel.parts
                            node = tree
                            for part in parts[:-1]:
                                node = node.setdefault(part, {})
                            node[parts[-1]] = None
                        return {"ok": True, "tool": call.tool, "result": {"tree": tree}}
                    case "active_file_get_path":
                        return {"ok": True, "tool": call.tool, "result": {"error": "no active file tracker — use vault_read with a path"}}
                    case "periodic_note_get_path":
                        return {"ok": True, "tool": call.tool, "result": {"error": "no periodic note tracker — use vault_write to create one"}}
                    case "search_query":
                        root = _VAULT_BASE
                        query = call.args.get("query", "")
                        results = []
                        for p in root.rglob("*.md"):
                            text = p.read_text(encoding="utf-8", errors="replace")
                            if query.lower() in text.lower():
                                idx = text.lower().index(query.lower())
                                start = max(0, idx - 60)
                                end = min(len(text), idx + 140)
                                results.append({"path": str(p.relative_to(root)), "snippet": text[start:end]})
                        _log_audit(_AuditEvent(
                            actor=actor,
                            action="search_query",
                            target=query,
                            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
                            result=f"{len(results[:20])} matches",
                        ))
                        return {"ok": True, "tool": call.tool, "result": {"matches": results[:20]}}
                    case "search_simple":
                        root = _VAULT_BASE
                        query = call.args.get("query", "")
                        results = []
                        for p in root.rglob("*.md"):
                            text = p.read_text(encoding="utf-8", errors="replace")
                            if query.lower() in text.lower():
                                results.append({"path": str(p.relative_to(root)), "score": 1.0})
                        _log_audit(_AuditEvent(
                            actor=actor,
                            action="search_simple",
                            target=query,
                            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
                            result=f"{len(results[:20])} matches",
                        ))
                        return {"ok": True, "tool": call.tool, "result": {"matches": results[:20]}}
                    case "tag_list":
                        root = _VAULT_BASE
                        tags = set()
                        for p in root.rglob("*.md"):
                            text = p.read_text(encoding="utf-8", errors="replace")
                            for m in re.finditer(r"#([a-zA-Z][a-zA-Z0-9_/-]+)", text):
                                tags.add(m.group(1))
                        _log_audit(_AuditEvent(
                            actor=actor,
                            action="tag_list",
                            target=str(root),
                            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
                            result=f"{len(tags)} tags",
                        ))
                        return {"ok": True, "tool": call.tool, "result": {"tags": sorted(tags)}}
                    case "command_list":
                        return {"ok": True, "tool": call.tool, "result": {"commands": []}}
                    case "command_execute":
                        return {"ok": True, "tool": call.tool, "result": {"error": "no Obsidian UI connected"}}
                    case "open_file":
                        target = _normalize_vault_path(call.args.get("path", ""))
                        if not target.exists():
                            raise HTTPException(status_code=404, detail=f"File not found: {target}")
                        _log_audit(_AuditEvent(
                            actor=actor,
                            action="open_file",
                            target=str(target),
                            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
                            result="success",
                        ))
                        return {"ok": True, "tool": call.tool, "result": {"opened": str(target), "note": "no UI connected — path returned for reference"}}
                    case "graph_ingest":
                        r = await client.post("/graph/ingest", json=call.args)
                    case "graph_get":
                        r = await client.get(f"/graph/{call.args.get('session', '')}")
                    case "graph_top":
                        r = await client.get(f"/graph/{call.args.get('session', '')}/top?k={call.args.get('k', 20)}")
                    case "graph_sessions":
                        r = await client.get("/graph")
                    case _:
                        raise HTTPException(status_code=404, detail=f"Unknown tool: {call.tool}")

                r.raise_for_status()
                try:
                    return {"ok": True, "tool": call.tool, "result": r.json()}
                except Exception:
                    return {"ok": True, "tool": call.tool, "result": r.text}

            except httpx.HTTPStatusError as exc:
                status = f"upstream_error:{exc.response.status_code}"
                raise HTTPException(status_code=exc.response.status_code, detail=exc.response.text) from exc
            except httpx.RequestError as exc:
                status = f"upstream_error:{exc}"
                raise HTTPException(status_code=502, detail=f"Upstream error: {exc}") from exc
    finally:
        _log_audit(_AuditEvent(
            actor=actor,
            action=f"tool:{call.tool}",
            target=str(call.args),
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
            result=status,
        ))


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
