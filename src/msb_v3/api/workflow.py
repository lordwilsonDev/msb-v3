"""Workflow router — POST /workflow/advance (the Task Contract API).

The API counterpart to /conversation/ask (docs/task-contract-v1.md): one
synchronous endpoint that advances exactly ONE READY dag node under its
contract, emits the §8 ledger evidence + TASK_FAILED event, and returns the
advanced dag + the executed node's ledger verdicts. The client holds the dag
and re-POSTs the returned dag to drive the chain (spec §9 pinned granularity
— stateless HTTP, one node per call).

Execution is deterministic and zero-spend: the runner is the pluggable hook
(StubRunner today — the domain-router dispatch lands behind this endpoint
later) and the contract's predicates run for real against what it wrote. The
ledger is the SAME ledger as the conversation producer (one ledger, two
producers).

    POST /workflow/advance
    {"dag": [...], "goal": "...", "tenant_id": "default", "run_id": "run-1"}
    -> {"schema_version": "1.0", "trace_id": "...", "status": "advanced"|"noop",
        "dag": [...updated...], "executed": {...verdicts...} | null}

Dag entries must carry `"status": "READY"` to be selectable (the executor
selects READY only — a status-less dag is a safe noop, never a silent run).
"""

from __future__ import annotations

import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from msb_v3.api.auth import check_auth
from msb_v3.conversation import executor, task_producer
from msb_v3.conversation.envelope import SCHEMA_VERSION, mint_trace_id
from msb_v3.conversation.task_contract import validate_dag

router = APIRouter(tags=["workflow"])

# Server-managed scratch space for task output (predicates verify files here).
_DEFAULT_OUTPUT_ROOT = Path.cwd() / ".workflow-runs"

_SAFE_RUN_ID = re.compile(r"[^A-Za-z0-9._-]")


class WorkflowAdvanceRequest(BaseModel):
    dag: list[dict[str, Any]] = Field(
        ...,
        description=(
            "dag entries under the Task Contract; set status: READY to be "
            "selectable (a status-less dag is a safe noop)"
        ),
    )
    goal: Optional[str] = None
    tenant_id: str = "default"
    trace_id: Optional[str] = None
    run_id: Optional[str] = None
    dry_run: bool = False


def _output_root_for(tenant_id: str, run_id: Optional[str]) -> Path:
    """Run-scoped scratch root, TENANT-SCOPED: runs/<tenant>/<run_id> so one
    tenant's leftover writes can never satisfy another tenant's predicates
    (reviewer finding — the workspace was tenant-blind). A stable per-run
    workspace survives across advance calls of the same run (stateless HTTP,
    stateful workspace); a fresh temp dir when no run_id is given. Tenants
    and run ids are sanitized and contained — a traversal attempt falls back
    to scratch, never escapes the runs root."""
    base = Path(os.getenv("MSB_WORKFLOW_OUTPUT_ROOT", str(_DEFAULT_OUTPUT_ROOT))).resolve()
    if not run_id:
        return Path(tempfile.mkdtemp(prefix="workflow-advance-"))
    t_safe = _SAFE_RUN_ID.sub("_", tenant_id or "default")
    safe = _SAFE_RUN_ID.sub("_", run_id)
    if safe in (".", "..") or t_safe in (".", ".."):
        # a dot segment would collapse onto the base (or above) — never a
        # valid workspace id; fall back to scratch, never resolve it
        return Path(tempfile.mkdtemp(prefix="workflow-advance-"))
    root = (base / t_safe / safe).resolve()
    try:
        inside = os.path.commonpath([str(base), str(root)]) == str(base)
    except ValueError:
        inside = False
    if not inside:
        return Path(tempfile.mkdtemp(prefix="workflow-advance-"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def _error_body(trace_id: str, code: str, message: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "trace_id": trace_id,
        "status": "error",
        "error": {"code": code, "message": message},
    }


def _envelope_error(status_code: int, trace_id: str, code: str, message: str) -> JSONResponse:
    """Error responses carry the envelope body directly, not wrapped in
    FastAPI's {'detail': ...} (mirrors /conversation/ask spec §8)."""
    return JSONResponse(status_code=status_code, content=_error_body(trace_id, code, message))


# response_model=None: the handler returns a mix of raw dicts (envelope
# bodies) and JSONResponse (error bodies) — FastAPI must not build a Pydantic
# model from the union annotation (same pattern as /conversation/ask).
@router.post("/advance", response_model=None)
async def workflow_advance(
    body: WorkflowAdvanceRequest, request: Request,
) -> dict[str, Any] | JSONResponse:
    """Advance exactly one READY dag node under its contract and return the
    advanced dag + ledger verdicts (spec §9 — one node per call)."""
    check_auth(request)
    trace_id = body.trace_id or mint_trace_id()
    started = time.perf_counter()

    if not body.dag:
        return _envelope_error(422, trace_id, "contract_invalid", "workflow.dag must be a non-empty list")

    errors = validate_dag(body.dag)
    if errors:
        return _envelope_error(422, trace_id, "contract_invalid", "; ".join(errors))

    output_root = _output_root_for(body.tenant_id, body.run_id)
    try:
        updated, result = executor.advance_dag(
            body.dag,
            runner=executor.StubRunner(output_root),
            output_root=output_root,
            ledger_dir=task_producer.default_ledger_dir(),
            git_head=task_producer.default_git_head(),
            tenant_id=body.tenant_id,
            goal=body.goal,
            dry_run=body.dry_run,
        )
    except ValueError as exc:  # contract error surfaced at execution — reject, never half-serve
        return _envelope_error(422, trace_id, "contract_invalid", str(exc))
    except OSError as exc:  # ledger or output-root I/O — retryable server failure
        return _envelope_error(503, trace_id, "server_io_failed", f"server I/O failure during execution: {exc}")

    latency_ms = int((time.perf_counter() - started) * 1000)
    if result is None:
        return {
            "schema_version": SCHEMA_VERSION,
            "trace_id": trace_id,
            "status": "noop",
            "dag": updated,
            "executed": None,
            "latency_ms": latency_ms,
            "note": "nothing READY to execute (preconditions unmet or all advanced)",
        }
    executed = {
        "task_id": result.task_id,
        "status": result.status,
        "failure_kind": result.failure_kind,
        "reason": result.reason,
        "claim_id": result.claim_id,
        "verdict": result.verdict,
        "evidence_ref": result.evidence_ref,
        "event": "TASK_FAILED" if result.event_ref else None,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "trace_id": trace_id,
        "status": "advanced",
        "dag": updated,
        "executed": executed,
        "latency_ms": latency_ms,
    }
