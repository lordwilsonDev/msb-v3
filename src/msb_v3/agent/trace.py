"""Agent trace — the evidence chain for one Handle-this run (blueprint §12-13).

Every significant stage becomes an auditable event in the UAC chain
(component="agentic"): run start (request + intent), the plan, the execution
(per-task grounded verification results), and the outcome. Enough to answer
"why did the system do that?" — the trace, not the verdict, is the evidence.

Replay determinism: `deterministic_hash` covers everything that must be
identical for an identical run (request, intent, plan, per-task outputs,
verdict) — timestamps and latency are excluded by construction, so the T1.7
slice gate can assert same-input -> same-hash.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from msb_v3.agent.dag import TaskGraph
from msb_v3.agent.executor import ExecReport
from msb_v3.agent.intent import Intent
from msb_v3.observability.metrics import Metrics
from msb_v3.uac.audit_chain import AuditChain


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
        execution.append(
            {
                "task_id": result.task_id,
                "ok": result.ok,
                "verification": result.verification,
                "error": result.error,
            }
        )

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
        },
        created_ts=_now(),
    )
    trace.deterministic_hash = _deterministic_hash(trace)
    return trace


def _deterministic_hash(trace: AgentTrace) -> str:
    """Hash the replay-deterministic content only (no timestamps/latency)."""
    payload = json.dumps(
        {
            "request": trace.request,
            "intent": trace.intent,
            "graph_source": trace.graph_source,
            "tasks": trace.tasks,
            "execution": trace.execution,
            "verdict": trace.verdict,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def record_trace(trace: AgentTrace, audit_chain: Optional[AuditChain] = None) -> None:
    """Append the trace's stages to the UAC audit chain (component="agentic").

    One event per evidence stage: run start, plan, execution, outcome.
    """
    chain = audit_chain if audit_chain is not None else AuditChain()
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
    Metrics.inc("agentic", "trace:recorded")
