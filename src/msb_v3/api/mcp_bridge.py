"""HTTP bridge for Make.com and other HTTP-only clients to call MCP-like tools."""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from msb_v3 import __version__
from msb_v3.core.config import settings
from msb_v3.observability.metrics import Metrics

logger = logging.getLogger(__name__)

router = APIRouter()

BASE_URL = os.getenv("MSB_MCP_BASE_URL", "http://127.0.0.1:8766")
REQUEST_TIMEOUT = int(os.getenv("MSB_MCP_REQUEST_TIMEOUT", "120"))
# Import-time default keeps the attribute patchable in tests; the live secret
# is re-read from the environment on every auth check so a config change
# applies without a restart (same live-read contract as api/auth.py).
_MCP_BRIDGE_SECRET = os.getenv("MCP_BRIDGE_SECRET", "")
# Vault root from config (MSB_VAULT_PATH env or ~/Documents/Vault default), not
# a hardcoded machine home — the containment check in _normalize_vault_path
# pins every vault_* tool to this root.
_VAULT_BASE = Path(settings.vault_path).resolve()
_VERIFY_BUILD_ECHO_DIR = Path(os.path.expanduser("~/.local/share/msb-v3/verify-build"))
_AUDIT_LOGGER = logging.getLogger("msb_v3.mcp_audit")
# MCP bridge capability grant (M2/P1 hardening, 2026-08-17). The vault_*
# mutations route through the governed tool loop; this is the bridge caller's
# standing grant. Empty (the default) = read-only — a mutation without an
# explicit grant is DENIED and audited with a verdict, exactly like the chat
# surface. Operators who want Make.com workflows to write the vault must set
# MSB_MCP_GRANTED_CAPABILITIES (comma-separated capability ids, e.g.
# vault.write).
_MCP_GRANTED_CAPABILITIES = frozenset(
    c.strip()
    for c in os.getenv("MSB_MCP_GRANTED_CAPABILITIES", "").split(",")
    if c.strip()
)


def _run_governed_proxy(tool_id: str, args: dict[str, Any]) -> str:
    """Route a bridge tool call through the governed tool loop.

    Same gate as the chat surface (tools.runtime._run_governed): capability
    grant + approval gate + contained executor + UAC-chain audit carrying an
    explicit verdict. No grant = fail-closed deny. The bridge-level audit
    event is still logged by the caller for actor/timestamp context.
    """
    from msb_v3.tools.runtime import _run_governed

    return _run_governed(
        tool_id,
        args,
        granted=_MCP_GRANTED_CAPABILITIES,
        tenant="mcp-bridge",
        session="mcp-bridge",
    )
# verify_build ids are used directly as filenames; allow only safe characters
# so a caller can never inject path separators or control characters.
_SAFE_BUILD_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
# Strip control characters / newlines from values that get embedded verbatim
# into files written by verify_build (echo receipt + vault note).
_STRIP_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _bridge_secret() -> str:
    """Live MCP bridge secret: environment wins, import-time attr as fallback.

    The fallback keeps ``_MCP_BRIDGE_SECRET`` patchable in tests while the
    production read follows the environment at request time."""
    return os.getenv("MCP_BRIDGE_SECRET", "") or _MCP_BRIDGE_SECRET


def _safe_text(value: str) -> str:
    """Collapse control characters so untrusted strings written into files
    cannot inject newlines or escape the intended single-line format."""
    return _STRIP_CONTROL.sub(" ", value)


class _AuditEvent(BaseModel):
    actor: str
    action: str
    target: str
    timestamp: str
    result: str


def _log_audit(event: _AuditEvent) -> None:
    try:
        _AUDIT_LOGGER.info(json.dumps(event.model_dump(), ensure_ascii=False))
    except Exception as exc:
        logger.warning("audit log write failed: %s", exc)


def _client_identity(request: Request) -> str:
    # Audit identity only — never trust X-Forwarded-For (any caller could
    # spoof it); the socket peer is the only reliable actor signal.
    if request.client:
        return request.client.host
    return "unknown"


def _check_auth(request: Request) -> None:
    # Constant-time comparison: the secret is a high-entropy shared token, but
    # the gate should never leak timing information about a prefix match.
    header = request.headers.get("x-mcp-secret", "")
    if not header or not secrets.compare_digest(header.encode("utf-8"), _bridge_secret().encode("utf-8")):
        raise HTTPException(status_code=401, detail="unauthorized")


def _normalize_path_list(raw: Any) -> list[str]:
    """Accept either a real list (raw HTTP callers can send true JSON arrays)
    or a comma-separated string (what a real MCP tool call actually sends --
    mcp_adapter.py's generic tools/list schema declares every argument as a
    plain string, so files/tests arrive this way in practice, not as JSON
    arrays)."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    return [piece.strip() for piece in str(raw).split(",") if piece.strip()]


def _normalize_vault_path(raw: str) -> Path:
    """Resolve a vault-relative path and enforce real containment.

    Uses ``relative_to`` instead of a string-prefix check: a prefix check
    lets a sibling directory that merely shares the vault's name prefix
    (e.g. ``../Vault2/x``) pass containment. ``relative_to`` fails for any
    resolved path that is not actually inside the vault root.
    """
    if raw is None:
        raw = ""
    path = (_VAULT_BASE / raw).resolve()
    try:
        path.relative_to(_VAULT_BASE)
    except ValueError:
        raise HTTPException(status_code=400, detail="path traversal detected")
    return path


def _codegraph_int(raw: Any, default: int) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _codegraph_store() -> Any:
    """Lazy store/query access — the graph DB path is config-driven."""
    from msb_v3.codegraph.queries import CodeGraphQueries
    from msb_v3.codegraph.store import CodeGraphStore

    return CodeGraphQueries(CodeGraphStore(settings.codegraph_db_path))


def _codegraph_stats(repo: str) -> dict[str, Any]:
    return _codegraph_store().store.stats(repo)


def _codegraph_symbols(repo: str, name: str) -> list[dict[str, Any]]:
    return _codegraph_store().find_symbol(repo, name, limit=10)


def _codegraph_context(repo: str, symbol: str) -> dict[str, Any]:
    return _codegraph_store().context_of(repo, symbol)


def _codegraph_impact(repo: str, file: str, line: int) -> dict[str, Any]:
    return _codegraph_store().impact_of(repo, file, line=line)


def _codegraph_rename(repo: str, name: str) -> dict[str, Any]:
    return _codegraph_store().rename_preview(repo, name)


# --- Memory Fabric helpers (spec §4.2.2) -----------------------------------
# recall is read-only; store/verify/forget/consolidate mutate the fabric.
# The bridge is secret-gated (same as every other tool); verification and
# consolidation are audited state changes, which is exactly what the fabric
# is for — so they are allowed here, but never silently: every one returns
# the resulting state so the caller sees what happened.


def _memory_fabric() -> Any:
    from msb_v3.memory_fabric.fabric import MemoryFabric
    from msb_v3.memory_fabric.store import MemoryFabricStore

    return MemoryFabric(MemoryFabricStore(settings.memory_fabric_db_path))


def _mf_store(args: dict[str, Any]) -> dict[str, Any]:
    from msb_v3.memory_fabric.models import MemoryType

    content = str(args.get("content") or "")
    type_raw = str(args.get("type") or "semantic")
    try:
        type_ = MemoryType(type_raw)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"unknown type: {type_raw}")
    tags_raw = args.get("tags") or []
    tags = [str(t).strip() for t in tags_raw if str(t).strip()] if isinstance(tags_raw, list) else []
    item = _memory_fabric().store_memory(
        content,
        type_=type_,
        tags=tags,
        importance=float(args.get("importance") or 0.5),
        source_agent=str(args.get("source_agent") or ""),
        source="mcp-bridge",
        task_id=str(args.get("task_id") or ""),
        tenant=str(args.get("tenant") or "default"),
        project=str(args.get("project") or ""),
        tech=str(args.get("tech") or ""),
    )
    return item.as_dict()


def _mf_recall(args: dict[str, Any]) -> dict[str, Any]:
    query = str(args.get("query") or "")
    if not query.strip():
        raise HTTPException(status_code=422, detail="query required")
    try:
        top_k = min(max(int(args.get("top_k") or 8), 1), 50)
    except (TypeError, ValueError):
        top_k = 8
    hits = _memory_fabric().recall_memories(
        query,
        tenant=str(args.get("tenant") or "default"),
        project=str(args.get("project") or "").strip() or None,
        tech=str(args.get("tech") or "").strip() or None,
        top_k=top_k,
    )
    return {"count": len(hits), "memories": [h.as_dict() for h in hits]}


def _mf_verify(args: dict[str, Any]) -> dict[str, Any]:
    from msb_v3.memory_fabric.models import VerificationState

    memory_id = str(args.get("memory_id") or "")
    state_raw = str(args.get("to_state") or "")
    if not memory_id or not state_raw:
        raise HTTPException(status_code=422, detail="memory_id and to_state required")
    try:
        to_state = VerificationState(state_raw)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"unknown state: {state_raw}")
    item = _memory_fabric().verify_memory(
        memory_id, to_state, by=str(args.get("by") or "operator"), reason=str(args.get("reason") or "")
    )
    return item.as_dict()


def _mf_forget(args: dict[str, Any]) -> dict[str, Any]:
    memory_id = str(args.get("memory_id") or "")
    if not memory_id:
        raise HTTPException(status_code=422, detail="memory_id required")
    item = _memory_fabric().forget_memory(
        memory_id, by=str(args.get("by") or "operator"), reason=str(args.get("reason") or "forgotten")
    )
    return item.as_dict()


def _mf_consolidate(args: dict[str, Any]) -> dict[str, Any]:
    return _memory_fabric().consolidate(
        str(args.get("tenant") or "default"), by=str(args.get("by") or "operator")
    )


# --- Context Engine helper (spec §4.2.3) -----------------------------------


def _context_compose(args: dict[str, Any]) -> dict[str, Any]:
    from msb_v3.fabric.context_engine import ContextEngine

    task = str(args.get("task") or "")
    if not task.strip():
        raise HTTPException(status_code=422, detail="task required")
    try:
        budget = max(200, min(int(args.get("budget_tokens") or 4000), 20000))
    except (TypeError, ValueError):
        budget = 4000
    pkg = ContextEngine().compose(
        task.strip(),
        tenant=str(args.get("tenant") or "default"),
        session=str(args.get("session") or "default"),
        repo=str(args.get("repo") or "").strip() or None,
        project=str(args.get("project") or "").strip() or None,
        tech=str(args.get("tech") or "").strip() or None,
        budget_tokens=budget,
    )
    return pkg.as_dict()


# --- MoIE helper (spec §3, §23-25; Phase 3) ---------------------------------


def _moie_analyze(args: dict[str, Any]) -> dict[str, Any]:
    from msb_v3.moie import MoIEController

    claim = str(args.get("claim") or "").strip()
    if not claim:
        raise HTTPException(status_code=422, detail="claim required")
    domains = args.get("domains") or []
    if not isinstance(domains, list):
        domains = []
    decision = MoIEController(tenant=str(args.get("tenant") or "default")).analyze(
        claim,
        context={
            "domains": [str(d).strip() for d in domains if str(d).strip()],
            "thorough": bool(args.get("thorough", False)),
            "high_impact": bool(args.get("high_impact", False)),
        },
    )
    return decision.as_dict()


# --- Software Factory helper (spec §4.2.6, P3) ------------------------------


async def _factory_run(args: dict[str, Any]) -> dict[str, Any]:
    import os

    from msb_v3.factory import Builder, CliAgentBuilder, PatchBuilder, SoftwareFactory
    from msb_v3.factory.models import Issue

    title = str(args.get("title") or "").strip()
    repo = str(args.get("repo") or "").strip()
    if not title or not repo:
        raise HTTPException(status_code=422, detail="title and repo required")
    if not os.path.isdir(repo):
        raise HTTPException(status_code=422, detail=f"repo is not a directory: {repo}")
    builder_name = str(args.get("builder") or "cli")
    builder: Builder
    if builder_name == "patch":
        script = str(args.get("patch_script") or "").strip()
        if not script or not os.path.isfile(script):
            raise HTTPException(status_code=422, detail="builder=patch requires patch_script")
        builder = PatchBuilder(script)
    elif builder_name == "cli":
        builder = CliAgentBuilder()
    else:
        raise HTTPException(status_code=422, detail=f"unknown builder: {builder_name}")
    issue = Issue(title=title, body=str(args.get("body") or ""), repo=repo)
    run = await SoftwareFactory(builder=builder).process_issue(issue, repo=repo)
    return run.as_dict()


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
        # Forward the bridge secret on upstream calls: /chat and /memory are
        # auth-gated the same way /mcp is, so the proxy must present the
        # credential itself instead of leaving the caller's secret unproxied.
        upstream_headers = {}
        if _bridge_secret():
            upstream_headers["x-mcp-secret"] = _bridge_secret()
        async with httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=REQUEST_TIMEOUT,
            headers=upstream_headers,
        ) as client:
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
                    case "vault_write" | "vault_append" | "vault_patch" | "vault_delete" | "vault_move":
                        # Governed vault mutations (M2/P1 hardening, 2026-08-17):
                        # these used to execute file I/O directly with only auth
                        # — the live-loop test proved the gap (an unprivileged
                        # vault_write actually wrote to the vault). Now they
                        # route through the same governed loop as the chat
                        # surface (_run_governed: capability gate + approval
                        # gate + contained executor + UAC-chain audit with
                        # verdict). No grant = fail-closed deny, matching the
                        # chat path.
                        outcome = _run_governed_proxy(call.tool, call.args)
                        return {"ok": True, "tool": call.tool, "result": {"governed": outcome}}
                    case "vault_get_document_map":
                        root = _normalize_vault_path(call.args.get("path", ""))
                        if not root.exists() or not root.is_dir():
                            raise HTTPException(status_code=404, detail=f"Directory not found: {call.args.get('path')}")
                        tree: dict[str, Any] = {}
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
                        # Semantic-first search: /rag/search (Qdrant + embeddings)
                        # handles multi-word and phrase queries the old literal
                        # substring scan could not ("sovereign core architecture"
                        # used to return [] while the words sat in the vault). On
                        # any failure — qdrant down, collection missing, embed
                        # error — fall back to the substring scan, so the tool
                        # degrades loudly (mode: substring) rather than silently
                        # returning no results. A JSON null query must behave
                        # like an empty one (str(None) == "None" would search
                        # for the literal word "None").
                        query = str(call.args.get("query") or "")
                        if not query.strip():
                            return {"ok": True, "tool": call.tool, "result": {"matches": [], "mode": "empty", "note": "empty query"}}
                        tenant = str(call.args.get("tenant", "wilson-vault"))
                        try:
                            limit = min(int(call.args.get("limit", 20)), 50)
                        except (TypeError, ValueError):
                            limit = 20
                        results: list[dict[str, Any]] = []
                        mode = "substring"
                        try:
                            r = await client.post("/rag/search", json={"tenant_id": tenant, "query": query, "limit": limit})
                            r.raise_for_status()
                            hits = r.json().get("results", [])
                            if hits:
                                mode = "semantic"
                                results = [
                                    {
                                        "path": h.get("source", ""),
                                        "snippet": (h.get("text", "") or "")[:200],
                                        "score": round(float(h.get("score", 0.0)), 4),
                                    }
                                    for h in hits
                                ]
                        except (httpx.HTTPError, ValueError, KeyError):
                            # Real failure modes (network, 404/501, bad JSON,
                            # missing key) degrade to substring; genuine coding
                            # bugs still raise instead of being masked.
                            results = []
                        if not results:
                            # Substring fallback (the old behavior): literal
                            # phrase match with a context window.
                            for p in _VAULT_BASE.rglob("*.md"):
                                text = p.read_text(encoding="utf-8", errors="replace")
                                q = query.lower()
                                if q in text.lower():
                                    idx = text.lower().index(q)
                                    start = max(0, idx - 60)
                                    end = min(len(text), idx + 140)
                                    results.append({
                                        "path": str(p.relative_to(_VAULT_BASE)),
                                        "snippet": text[start:end],
                                        "score": None,
                                    })
                        _log_audit(_AuditEvent(
                            actor=actor,
                            action="search_query",
                            target=query,
                            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
                            result=f"{len(results[:20])} matches ({mode})",
                        ))
                        return {"ok": True, "tool": call.tool, "result": {"matches": results[:20], "mode": mode}}
                    case "search_simple":
                        # Retired: it returned literal substring matches with a
                        # hardcoded score of 1.0 and firehosed on empty queries.
                        # search_query now does semantic search with a substring
                        # fallback. Kept as a dispatch case so downstream callers
                        # get a clear signal instead of a silent 404.
                        _log_audit(_AuditEvent(
                            actor=actor,
                            action="search_simple",
                            target="retired",
                            timestamp=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
                            result="retired — use search_query",
                        ))
                        return {"ok": True, "tool": call.tool, "result": {
                            "retired": True,
                            "note": "search_simple is retired — use search_query (semantic via /rag/search, substring fallback).",
                        }}
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
                    case "verify_build":
                        build_id = call.args.get("id", "")
                        files = _normalize_path_list(call.args.get("files"))
                        tests = _normalize_path_list(call.args.get("tests"))
                        if not build_id:
                            raise HTTPException(status_code=400, detail="id required")
                        if not _SAFE_BUILD_ID.fullmatch(build_id):
                            raise HTTPException(
                                status_code=400,
                                detail="build id may only contain letters, digits, '.', '_', '-'",
                            )
                        if not files and not tests:
                            raise HTTPException(status_code=400, detail="at least one of files or tests required")

                        missing_files = [f for f in files if not Path(f).is_file()]
                        missing_tests = [t for t in tests if not Path(t).is_file()]
                        if missing_files or missing_tests:
                            return {
                                "ok": True,
                                "tool": call.tool,
                                "result": {
                                    "status": "FAILED",
                                    "missing_files": missing_files,
                                    "missing_tests": missing_tests,
                                },
                            }

                        timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started))
                        # file/test paths are caller-controlled and land verbatim
                        # in the echo receipt + vault note — collapse control
                        # characters so they cannot inject new content.
                        files_str = ", ".join(_safe_text(f) for f in files) if files else "(none)"
                        tests_str = ", ".join(_safe_text(t) for t in tests) if tests else "(none)"
                        echo_content = (
                            f"VERIFIED\nid: {build_id}\nfiles: {files_str}\n"
                            f"tests: {tests_str}\ntimestamp: {timestamp}\n"
                        )

                        _VERIFY_BUILD_ECHO_DIR.mkdir(parents=True, exist_ok=True)
                        echo_path = _VERIFY_BUILD_ECHO_DIR / f"{build_id}.txt"
                        echo_path.write_text(echo_content, encoding="utf-8")

                        vault_note = _normalize_vault_path("40_Memory/Verified-Builds-Log.md")
                        vault_note.parent.mkdir(parents=True, exist_ok=True)
                        with vault_note.open("a", encoding="utf-8") as f:
                            f.write(f"\n## {timestamp} — {build_id}\nFiles: {files_str}\nTests: {tests_str}\n")

                        _log_audit(_AuditEvent(
                            actor=actor,
                            action="verify_build",
                            target=build_id,
                            timestamp=timestamp,
                            result="verified",
                        ))
                        return {
                            "ok": True,
                            "tool": call.tool,
                            "result": {
                                "status": "VERIFIED",
                                "echo_path": str(echo_path),
                                "vault_note": "40_Memory/Verified-Builds-Log.md",
                            },
                        }
                    case "graph_ingest":
                        r = await client.post("/graph/ingest", json=call.args)
                    case "graph_get":
                        r = await client.get(f"/graph/{call.args.get('session', '')}")
                    case "graph_top":
                        r = await client.get(f"/graph/{call.args.get('session', '')}/top?k={call.args.get('k', 20)}")
                    case "graph_sessions":
                        r = await client.get("/graph")
                    # --- Code Graph (sovereign-architecture §4.2.1) ---
                    # Read-only repository intelligence, executed in-process
                    # against the local SQLite graph (the same containment
                    # as vault_read — no network hop, no source-tree access).
                    # Indexing stays operator-gated at POST /codegraph/index;
                    # these are queries only.
                    case "codegraph_stats":
                        repo = str(call.args.get("repo") or "")
                        if not repo:
                            raise HTTPException(status_code=400, detail="repo required")
                        return {"ok": True, "tool": call.tool, "result": _codegraph_stats(repo)}
                    case "codegraph_explore":
                        repo = str(call.args.get("repo") or "")
                        name = str(call.args.get("name") or "")
                        if not repo or not name:
                            raise HTTPException(status_code=400, detail="repo and name required")
                        return {"ok": True, "tool": call.tool, "result": {"symbols": _codegraph_symbols(repo, name)}}
                    case "codegraph_context":
                        repo = str(call.args.get("repo") or "")
                        symbol = str(call.args.get("symbol") or "")
                        if not repo or not symbol:
                            raise HTTPException(status_code=400, detail="repo and symbol required")
                        return {"ok": True, "tool": call.tool, "result": _codegraph_context(repo, symbol)}
                    case "codegraph_impact":
                        repo = str(call.args.get("repo") or "")
                        file = str(call.args.get("file") or "")
                        if not repo or not file:
                            raise HTTPException(status_code=400, detail="repo and file required")
                        line = _codegraph_int(call.args.get("line"), 0)
                        return {"ok": True, "tool": call.tool, "result": _codegraph_impact(repo, file, line)}
                    case "codegraph_rename":
                        repo = str(call.args.get("repo") or "")
                        name = str(call.args.get("name") or "")
                        if not repo or not name:
                            raise HTTPException(status_code=400, detail="repo and name required")
                        return {"ok": True, "tool": call.tool, "result": _codegraph_rename(repo, name)}
                    # --- Memory Fabric (spec §4.2.2) ---
                    case "memory_store":
                        return {"ok": True, "tool": call.tool, "result": _mf_store(call.args)}
                    case "memory_recall":
                        return {"ok": True, "tool": call.tool, "result": _mf_recall(call.args)}
                    case "memory_verify":
                        return {"ok": True, "tool": call.tool, "result": _mf_verify(call.args)}
                    case "memory_forget":
                        return {"ok": True, "tool": call.tool, "result": _mf_forget(call.args)}
                    case "memory_consolidate":
                        return {"ok": True, "tool": call.tool, "result": _mf_consolidate(call.args)}
                    case "context_compose":
                        return {"ok": True, "tool": call.tool, "result": _context_compose(call.args)}
                    case "moie_analyze":
                        return {"ok": True, "tool": call.tool, "result": _moie_analyze(call.args)}
                    case "factory_run":
                        return {"ok": True, "tool": call.tool, "result": await _factory_run(call.args)}
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


_MCP_TOOLS: list[dict[str, Any]] = [
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
    {"name": "search_query", "description": "Semantic vault search (Qdrant + embeddings, substring fallback)", "args": ["query", "tenant", "limit"]},
    {"name": "tag_list", "description": "List all tags in vault", "args": []},
    {"name": "command_list", "description": "List Obsidian commands", "args": []},
    {"name": "command_execute", "description": "Execute Obsidian command", "args": ["id"]},
    {"name": "open_file", "description": "Open file in Obsidian UI", "args": ["path"]},
    {"name": "verify_build", "description": "Verify claimed files/tests exist; echo locally and to vault only if verified", "args": ["id", "files", "tests"]},
    {"name": "codegraph_stats", "description": "Code Graph index stats for a repo", "args": ["repo"]},
    {"name": "codegraph_explore", "description": "Search a repo's symbol index (functions/classes/methods with locations)", "args": ["repo", "name"]},
    {"name": "codegraph_context", "description": "One symbol's definition + callers + callees", "args": ["repo", "symbol"]},
    {"name": "codegraph_impact", "description": "Blast-radius: who a change to a file/line would affect", "args": ["repo", "file", "line"]},
    {"name": "codegraph_rename", "description": "Rename preview: every reference a rename would touch (read-only)", "args": ["repo", "name"]},
    {"name": "memory_store", "description": "Store a memory in the fabric (episodic/semantic/procedural/architectural)", "args": ["content", "type", "tags", "importance", "source_agent", "project", "tech", "tenant"]},
    {"name": "memory_recall", "description": "Recall memories ranked for a query", "args": ["query", "project", "tech", "top_k", "tenant"]},
    {"name": "memory_verify", "description": "Transition a memory's verification state (UNVERIFIED/VERIFIED/CONTRADICTED/DEPRECATED)", "args": ["memory_id", "to_state", "by", "reason"]},
    {"name": "memory_forget", "description": "Soft-delete a memory (archived + DEPRECATED record)", "args": ["memory_id", "reason"]},
    {"name": "memory_consolidate", "description": "Merge duplicate memories + decay everything for a tenant", "args": ["tenant"]},
    {"name": "context_compose", "description": "Compose a layered token-budgeted context (L0-L7) for a task", "args": ["task", "repo", "project", "tech", "budget_tokens", "tenant"]},
    {"name": "moie_analyze", "description": "Run Mixture-of-Inversion-Experts on a claim: fail-closed verdict (APPROVE/CONDITIONAL/BLOCK) + contradictions + IDS", "args": ["claim", "domains", "thorough", "high_impact", "tenant"]},
    {"name": "factory_run", "description": "Run the Software Factory on an issue (classify → plan → build → test → review → verify) with a verdict + evidence chain; builder cli or patch", "args": ["title", "body", "repo", "labels", "builder", "patch_script"]},
]


@router.get("/status")
async def mcp_status(request: Request) -> dict[str, Any]:
    """Bridge status — identity, readiness, and the tool manifest size.

    Auth-gated like /tools and /proxy: the bridge surface is internal
    knowledge. Also gives health checks a probe that proves the auth gate
    itself is wired (a 401 here means a misconfigured secret, not a dead
    bridge)."""
    _check_auth(request)
    return {
        "service": "msb-v3",
        "version": __version__,
        "ready": bool(Metrics._ready),
        "tools": len(_MCP_TOOLS),
    }


@router.get("/tools")
async def list_tools(request: Request) -> dict[str, Any]:
    """Return available MCP-like tools for Make.com / Claude Code discovery.
    Auth-gated like /proxy — the tool manifest is internal knowledge."""
    _check_auth(request)
    return {"tools": _MCP_TOOLS}
