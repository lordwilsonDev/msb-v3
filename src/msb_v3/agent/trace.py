"""Agent trace — the evidence chain for one Handle-this run (blueprint §12-13).

Every significant stage becomes an auditable event in the UAC chain
(component="agentic"): run start (request + intent), the plan, the execution
(per-task grounded verification results), and the outcome. Enough to answer
"why did the system do that?" — the trace, not the verdict, is the evidence.

Replay determinism: `deterministic_hash` is content-addressed — it covers
everything that must be identical for an identical run (request, intent,
plan, per-task outputs, verdict) and excludes timestamps/latency by
construction. Same evidence -> same hash; evidence that differs (a live model
producing different output) legitimately yields a different hash. The hash is
a pure function of the evidence, so it can always be recomputed from the
recorded trace to prove it was not tampered with (see
`compute_deterministic_hash`).
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from msb_ledger.audit_chain import AuditChainLike
from msb_ledger.chain_anchor import anchored_chain_from_env
from msb_v3.agent.dag import TaskGraph
from msb_v3.agent.executor import ExecReport
from msb_v3.agent.intent import Intent
from msb_v3.observability.metrics import Metrics

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # pragma: no cover — import guard for circular safety
    from msb_v3.runtime.store import RuntimeStore


@dataclass
class AgentTrace:
    run_id: str
    request: str
    intent: Dict[str, Any] = field(default_factory=dict)
    graph_source: str = ""
    tasks: List[Dict[str, Any]] = field(default_factory=list)
    execution: List[Dict[str, Any]] = field(default_factory=list)
    verdict: str = "ERROR"  # PASS | FAIL | ERROR
    outcome: Dict[str, Any] = field(default_factory=dict)
    created_ts: str = ""
    deterministic_hash: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "request": self.request,
            "intent": self.intent,
            "graph_source": self.graph_source,
            "tasks": self.tasks,
            "execution": self.execution,
            "verdict": self.verdict,
            "outcome": self.outcome,
            "created_ts": self.created_ts,
            "deterministic_hash": self.deterministic_hash,
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_trace(
    run_id: str,
    request: str,
    intent: Intent,
    graph: TaskGraph,
    report: ExecReport,
) -> AgentTrace:
    """Assemble the trace for a completed run (pure, deterministic parts)."""
    verdict = "PASS" if report.ok else "FAIL"

    execution: List[Dict[str, Any]] = []
    for result in report.results:
        entry: Dict[str, Any] = {
            "task_id": result.task_id,
            "ok": result.ok,
            "verification": result.verification,
            "error": result.error,
        }
        # Phase 2: the context builder's eviction ledger is evidence — carry
        # it (when the chat tool produced one) so the trace answers "why does
        # the context look like this". It participates in the replay hash
        # (execution is hashed), so it is tamper-evident like the rest.
        for value in result.output.values():
            if isinstance(value, dict) and isinstance(value.get("context_ledger"), dict):
                entry["context_ledger"] = value["context_ledger"]
                break
        execution.append(entry)

    # Phase 1: cost logged per run. Token counts ride the task outputs (the
    # chat tool returns {"text", "prompt_tokens", "completion_tokens"}); sum
    # them and estimate cost at $0.001 / 1K completion tokens (the same
    # approximation ralph_loop uses — honest local-model costing).
    prompt_tokens = 0
    completion_tokens = 0
    for result in report.results:
        for value in result.output.values():
            if isinstance(value, dict):
                prompt_tokens += int(value.get("prompt_tokens", 0) or 0)
                completion_tokens += int(value.get("completion_tokens", 0) or 0)
    estimated_cost_usd = round((completion_tokens / 1000.0) * 0.001, 6)

    trace = AgentTrace(
        run_id=run_id,
        request=request,
        intent=intent.as_dict(),
        graph_source=graph.source,
        tasks=[t.as_dict() for t in graph.tasks],
        execution=execution,
        verdict=verdict,
        outcome={
            "skipped": list(report.skipped),
            "error": report.error,
            "total_latency_s": report.total_latency_s,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "estimated_cost_usd": estimated_cost_usd,
        },
        created_ts=_now(),
    )
    trace.deterministic_hash = _deterministic_hash(trace)
    return trace


def compute_deterministic_hash(fields: Dict[str, Any]) -> str:
    """Hash the replay-deterministic content of a run (no timestamps/latency).

    Public and dict-based so consumers (the acceptance gate, CI) can recompute
    the hash from a recorded trace and verify the evidence was not altered —
    the content-addressing property that makes the chain replayable.
    """
    payload = json.dumps(
        {
            "request": fields.get("request"),
            "intent": fields.get("intent"),
            "graph_source": fields.get("graph_source"),
            "tasks": fields.get("tasks"),
            "execution": fields.get("execution"),
            "verdict": fields.get("verdict"),
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _deterministic_hash(trace: AgentTrace) -> str:
    """Hash the replay-deterministic content of a built trace."""
    return compute_deterministic_hash(trace.as_dict())


def record_trace(
    trace: AgentTrace,
    audit_chain: Optional[AuditChainLike] = None,
    store: Optional[RuntimeStore] = None,
) -> None:
    """Record a run: append evidence events to the UAC audit chain (authoritative)
    and persist the trace row to the runtime store (queryable projection).

    One event per evidence stage: run start, plan, execution, outcome. Store
    persistence is best-effort — a store failure must never break the run
    (phase0-substrate-hardening.md, I7 note).
    """
    chain = audit_chain if audit_chain is not None else anchored_chain_from_env()
    chain.append(
        "agentic",
        "trace:run_start",
        {"run_id": trace.run_id, "request": trace.request, "intent": trace.intent},
    )
    chain.append(
        "agentic",
        "trace:plan",
        {"run_id": trace.run_id, "graph_source": trace.graph_source, "tasks": trace.tasks},
    )
    chain.append(
        "agentic",
        "trace:execution",
        {"run_id": trace.run_id, "execution": trace.execution},
    )
    chain.append(
        "agentic",
        "trace:outcome",
        {"run_id": trace.run_id, "verdict": trace.verdict, "outcome": trace.outcome},
    )
    if store is not None:
        try:
            store.save_trace(trace)
        except Exception as exc:  # noqa: BLE001 — best-effort projection
            # I7 note (phase0-substrate-hardening.md): a store failure must
            # never break the run. The chain events above are already written
            # and remain the authoritative record; this is a convenience
            # projection, so log and continue.
            logger.warning("trace store unavailable for run %s: %s", trace.run_id, exc)
    Metrics.inc("agentic", "trace:recorded")
