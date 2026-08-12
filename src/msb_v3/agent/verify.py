"""Grounded verification — the only verification the loop trusts (inversion A2).

No LLM judge anywhere in the verification path. Every verification_method
resolves to a deterministic, externally-checkable check:

    search_returned_hits -> did the search return >= 1 hit?
    synthesis_nonempty   -> is the output non-empty AND not a fallback?
    file_written         -> does the written file exist on disk with content?
    none                 -> pass-through (no verification declared)

The failure classifier implements the Recovery Architecture's classification
(blueprint §14) for loop-level decisions: transient -> retry, bad_tool ->
substitute, bad_retrieval -> retrieve again, permission -> escalate,
unsafe -> quarantine, unknown -> human review.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Dict, Optional

from msb_v3.agent.dag import Task

_FALLBACK_PREFIX = "[fallback]"


def _check_search_hits(output: Dict[str, Any]) -> Dict[str, Any]:
    for v in output.values():
        if isinstance(v, list) and len(v) >= 1:
            return {"ok": True, "detail": f"{len(v)} hits"}
        if isinstance(v, dict):
            for key in ("hits", "results", "matches"):
                val = v.get(key)
                if isinstance(val, list) and len(val) >= 1:
                    return {"ok": True, "detail": f"{len(val)} hits"}
    return {"ok": False, "detail": "search returned no hits"}


def _check_synthesis(output: Dict[str, Any]) -> Dict[str, Any]:
    for v in output.values():
        if isinstance(v, dict):
            v = v.get("text", "")
        if isinstance(v, str):
            text = v.strip()
            if text and not text.startswith(_FALLBACK_PREFIX):
                return {"ok": True, "detail": f"{len(text)} chars"}
            if text:
                return {"ok": False, "detail": "synthesis fell back (model unreachable)"}
    return {"ok": False, "detail": "synthesis output empty"}


def _check_file_written(output: Dict[str, Any]) -> Dict[str, Any]:
    for v in output.values():
        path = v if isinstance(v, str) else (v.get("path") if isinstance(v, dict) else None)
        if isinstance(path, str) and path:
            if os.path.exists(path) and os.path.getsize(path) > 0:
                return {"ok": True, "detail": f"file written: {path}"}
            if os.path.exists(path):
                return {"ok": False, "detail": f"file exists but empty: {path}"}
            return {"ok": False, "detail": f"file not found: {path}"}
    return {"ok": False, "detail": "no file path in task output"}


_CHECKS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    "search_returned_hits": _check_search_hits,
    "synthesis_nonempty": _check_synthesis,
    "file_written": _check_file_written,
    "none": lambda output: {"ok": True, "detail": "no verification"},
}


def verify_task(task: Task, output: Dict[str, Any]) -> Dict[str, Any]:
    """Grounded check for a task's output (registry dispatch)."""
    check = _CHECKS.get(task.verification_method)
    if check is None:
        return {"ok": False, "detail": f"unknown verification method: {task.verification_method}"}
    return check(output)


# ---------------------------------------------------------------------------
# Failure classification (Recovery Architecture §14)
# ---------------------------------------------------------------------------

_TRANSIENT = ("timed out", "connectionerror", "unreachable", "timeout", "econnrefused", "econnreset", "httperror")
_BAD_TOOL = ("tool-error", "unknown tool", "notimplemented")
_BAD_RETRIEVAL = ("no hits", "no results", "not found")
_PERMISSION = ("permission", "denied", "forbidden", "401", "403")
_UNSAFE = ("unsafe", "blocked", "quarantine")


def classify_failure(
    task: Task,
    output: Dict[str, Any],
    verification: Dict[str, Any],
    error: Optional[str] = None,
) -> str:
    """Classify a failed task for the recovery decision.

    Returns one of: transient | bad_tool | bad_retrieval | permission |
    unsafe | unknown. bad_plan is not deterministically detectable in v1
    (nothing grounded distinguishes a bad plan from a bad execution) — the
    loop treats unknown as bad-plan-or-escalate, matching §14's
    "unknown -> human review".
    """
    detail = " ".join(filter(None, [error or "", verification.get("detail", "")])).lower()
    if not detail:
        return "unknown"
    if any(m in detail for m in _UNSAFE):
        return "unsafe"
    if any(m in detail for m in _PERMISSION):
        return "permission"
    if any(m in detail for m in _TRANSIENT):
        return "transient"
    if any(m in detail for m in _BAD_TOOL):
        return "bad_tool"
    if any(m in detail for m in _BAD_RETRIEVAL):
        return "bad_retrieval"
    return "unknown"
