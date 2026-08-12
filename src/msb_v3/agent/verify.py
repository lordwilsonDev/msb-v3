"""Grounded verification — the only verification the loop trusts (inversion A2).

No LLM judge anywhere in the verification path. Every verification_method
resolves to a deterministic, externally-checkable check:

    search_returned_hits   -> did the search return >= 1 hit?
    synthesis_nonempty     -> is the output non-empty AND not a fallback?
    file_written           -> does the written file exist on disk with content?
    file_written_with_heading -> does the file exist, have content, and carry
                                 the expected markdown heading (Phase 1 task:
                                 "produce a vault note and verify it exists
                                 with the expected heading")?
    none                   -> pass-through (no verification declared)

Every result is a spec-shaped receipt (Sovereign-Agentic-Runtime-Build-Spec
§3.4): {"ok", "detail", "kind", "check", "trust"}. All of these checks are
GROUNDED (external ground truth) and HIGH-trust — the spec forbids an LLM
judge from ever being a sole gate, so no advisory receipt appears here.

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


def _receipt(ok: bool, detail: str, check: str) -> Dict[str, Any]:
    """Spec §3.4 Verification Receipt shape. All grounded checks are
    kind=grounded, trust=high, verdict=pass|fail, confidence=1.0 (a
    deterministic check is fully confident)."""
    return {
        "ok": ok,
        "detail": detail,
        "kind": "grounded",
        "check": check,
        "trust": "high",
        "verdict": "pass" if ok else "fail",
        "confidence": 1.0,
    }


def _check_search_hits(output: Dict[str, Any]) -> Dict[str, Any]:
    for v in output.values():
        if isinstance(v, list) and len(v) >= 1:
            return _receipt(True, f"{len(v)} hits", "search_returned_hits")
        if isinstance(v, dict):
            for key in ("hits", "results", "matches"):
                val = v.get(key)
                if isinstance(val, list) and len(val) >= 1:
                    return _receipt(True, f"{len(val)} hits", "search_returned_hits")
    return _receipt(False, "search returned no hits", "search_returned_hits")


def _check_synthesis(output: Dict[str, Any]) -> Dict[str, Any]:
    for v in output.values():
        if isinstance(v, dict):
            v = v.get("text", "")
        if isinstance(v, str):
            text = v.strip()
            if text and not text.startswith(_FALLBACK_PREFIX):
                return _receipt(True, f"{len(text)} chars", "synthesis_nonempty")
            if text:
                return _receipt(False, "synthesis fell back (model unreachable)", "synthesis_nonempty")
    return _receipt(False, "synthesis output empty", "synthesis_nonempty")


def _check_file_written(output: Dict[str, Any]) -> Dict[str, Any]:
    for v in output.values():
        path = v if isinstance(v, str) else (v.get("path") if isinstance(v, dict) else None)
        if isinstance(path, str) and path:
            if os.path.exists(path) and os.path.getsize(path) > 0:
                return _receipt(True, f"file written: {path}", "file_written")
            if os.path.exists(path):
                return _receipt(False, f"file exists but empty: {path}", "file_written")
            return _receipt(False, f"file not found: {path}", "file_written")
    return _receipt(False, "no file path in task output", "file_written")


def _check_file_written_with_heading(output: Dict[str, Any]) -> Dict[str, Any]:
    """Phase 1's canonical task: write a vault note and verify it exists WITH
    the expected heading. Grounded: the file must exist, be non-empty, and
    contain a markdown # heading line (the vault note's expected heading)."""
    for v in output.values():
        path = v if isinstance(v, str) else (v.get("path") if isinstance(v, dict) else None)
        if isinstance(path, str) and path:
            if not os.path.exists(path):
                return _receipt(False, f"file not found: {path}", "file_written_with_heading")
            if os.path.getsize(path) == 0:
                return _receipt(False, f"file exists but empty: {path}", "file_written_with_heading")
            with open(path, encoding="utf-8", errors="replace") as fh:
                first_lines = [line.strip() for line in fh.read().splitlines() if line.strip()]
            has_heading = bool(first_lines and first_lines[0].startswith("# "))
            if not has_heading:
                return _receipt(False, f"file has no leading markdown heading: {path}", "file_written_with_heading")
            return _receipt(True, f"file written with heading: {path}", "file_written_with_heading")
    return _receipt(False, "no file path in task output", "file_written_with_heading")


_CHECKS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    "search_returned_hits": _check_search_hits,
    "synthesis_nonempty": _check_synthesis,
    "file_written": _check_file_written,
    "file_written_with_heading": _check_file_written_with_heading,
    "none": lambda output: _receipt(True, "no verification", "none"),
}


def verify_task(task: Task, output: Dict[str, Any]) -> Dict[str, Any]:
    """Grounded check for a task's output (registry dispatch). Returns a
    spec-shaped receipt (§3.4): kind=grounded, trust=high, verdict, confidence."""
    check = _CHECKS.get(task.verification_method)
    if check is None:
        return _receipt(False, f"unknown verification method: {task.verification_method}", task.verification_method)
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
