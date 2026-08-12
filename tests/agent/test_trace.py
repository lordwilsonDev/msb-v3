"""Tests for the agent trace + evidence chain (msb_v3.agent.trace)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from msb_v3.agent.dag import Task, TaskGraph  # noqa: E402
from msb_v3.agent.executor import ExecReport, TaskResult  # noqa: E402
from msb_v3.agent.intent import Intent  # noqa: E402
from msb_v3.agent.trace import build_trace, record_trace  # noqa: E402


class _Audit:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def append(self, component: str, event_type: str, payload: Dict[str, Any]) -> None:
        self.events.append((component, event_type, payload))


def _report(ok: bool = True) -> ExecReport:
    if ok:
        results = (
            TaskResult(task_id="research", ok=True, output={"search_query": [{"id": "a"}]},
                       verification={"ok": True, "detail": "1 hits"}),
            TaskResult(task_id="synthesize", ok=True, output={"chat": "brief"},
                       verification={"ok": True, "detail": "5 chars"}),
        )
        return ExecReport(ok=True, goal="g", results=results)
    return ExecReport(
        ok=False,
        goal="g",
        results=(TaskResult(task_id="research", ok=False, output={}, verification={"ok": False, "detail": "no hits"}, error="x"),),
        skipped=("synthesize",),
        error="task research failed: x",
    )


def _graph() -> TaskGraph:
    return TaskGraph(
        goal="g",
        source="template",
        tasks=(
            Task(task_id="research", goal="gr", verification_method="search_returned_hits"),
            Task(task_id="synthesize", goal="gs", parent_id="research", verification_method="synthesis_nonempty"),
        ),
    )


def _intent() -> Intent:
    return Intent(request="research the vault", goals=("research the vault",), permissions=("read_vault",), source="llm")


def test_build_trace_shape() -> None:
    trace = build_trace("run-1", "research the vault", _intent(), _graph(), _report())
    assert trace.verdict == "PASS"
    assert trace.graph_source == "template"
    assert trace.intent["source"] == "llm"
    assert [t["task_id"] for t in trace.tasks] == ["research", "synthesize"]
    assert trace.execution[0]["verification"]["ok"] is True
    assert trace.outcome["error"] is None
    assert trace.created_ts  # timestamp present but excluded from the hash


def test_failed_run_verdict_is_fail_with_skipped() -> None:
    trace = build_trace("run-2", "x", _intent(), _graph(), _report(ok=False))
    assert trace.verdict == "FAIL"
    assert trace.outcome["skipped"] == ["synthesize"]
    assert trace.execution[0]["ok"] is False


def test_deterministic_hash_is_stable_and_sensitive() -> None:
    t1 = build_trace("run-1", "research the vault", _intent(), _graph(), _report())
    t2 = build_trace("run-2", "research the vault", _intent(), _graph(), _report())
    assert t1.deterministic_hash == t2.deterministic_hash  # different run_id, same content

    t3 = build_trace("run-3", "different request", _intent(), _graph(), _report())
    assert t1.deterministic_hash != t3.deterministic_hash

    # A different execution outcome must change the hash (verification is evidence).
    t4 = build_trace("run-4", "research the vault", _intent(), _graph(), _report(ok=False))
    assert t1.deterministic_hash != t4.deterministic_hash


def test_record_trace_writes_four_evidence_events() -> None:
    audit = _Audit()
    trace = build_trace("run-1", "x", _intent(), _graph(), _report())
    record_trace(trace, audit_chain=audit)

    assert len(audit.events) == 4
    assert [e[0] for e in audit.events] == ["agentic"] * 4
    assert [e[1] for e in audit.events] == ["trace:run_start", "trace:plan", "trace:execution", "trace:outcome"]
    # events are tied by run_id
    assert all(e[2]["run_id"] == "run-1" for e in audit.events)
    assert audit.events[3][2]["verdict"] == "PASS"


def test_trace_execution_surfaces_context_ledger() -> None:
    """Phase 2: the context builder's eviction ledger rides the chat task's
    output into the trace — evidence for "why does the context look like
    this" (inversion omission #5)."""
    ledger = {
        "budget_tokens": 120,
        "system_tokens": 3,
        "query_tokens": 2,
        "included_matches": 1,
        "evicted_matches": 1,
        "truncated": False,
        "total_tokens": 60,
        "items": [{"source": "a.md", "score": 0.9}],
    }
    results = (
        TaskResult(
            task_id="synthesize",
            ok=True,
            output={"chat": {"text": "brief", "context_ledger": ledger}},
            verification={"ok": True, "detail": "5 chars"},
        ),
    )
    report = ExecReport(ok=True, goal="g", results=results)
    trace = build_trace("run-c", "x", _intent(), _graph(), report)

    synth = next(e for e in trace.execution if e["task_id"] == "synthesize")
    assert synth["context_ledger"] == ledger
    assert synth["context_ledger"]["evicted_matches"] == 1


def test_context_ledger_participates_in_replay_hash() -> None:
    """The ledger is evidence: two runs identical except for the ledger must
    hash differently, and the recomputed hash from the trace must match."""
    from msb_v3.agent.trace import compute_deterministic_hash

    def _report_with(ledger: dict) -> ExecReport:
        return ExecReport(
            ok=True,
            goal="g",
            results=(
                TaskResult(
                    task_id="synthesize",
                    ok=True,
                    output={"chat": {"text": "brief", "context_ledger": ledger}},
                    verification={"ok": True, "detail": "5 chars"},
                ),
            ),
        )

    a = build_trace("r1", "x", _intent(), _graph(), _report_with({"included_matches": 1, "evicted_matches": 0}))
    b = build_trace("r2", "x", _intent(), _graph(), _report_with({"included_matches": 0, "evicted_matches": 2}))
    assert a.deterministic_hash != b.deterministic_hash

    # Content-addressed: recomputing from the recorded trace reproduces it.
    assert compute_deterministic_hash(a.as_dict()) == a.deterministic_hash


def test_trace_logs_cost_per_run() -> None:
    """Phase 1 acceptance: cost logged per run — token counts summed from task
    outputs, estimated cost at $0.001/1K completion tokens (ralph pattern)."""
    results = (
        TaskResult(
            task_id="synthesize",
            ok=True,
            output={
                "chat": {
                    "text": "brief",
                    "prompt_tokens": 1200,
                    "completion_tokens": 300,
                }
            },
            verification={"ok": True, "detail": "5 chars"},
        ),
    )
    report = ExecReport(ok=True, goal="g", results=results)
    trace = build_trace("run-c", "x", _intent(), _graph(), report)

    assert trace.outcome["prompt_tokens"] == 1200
    assert trace.outcome["completion_tokens"] == 300
    assert trace.outcome["estimated_cost_usd"] == round((300 / 1000.0) * 0.001, 6)
    assert trace.outcome["estimated_cost_usd"] > 0


def test_trace_zero_cost_when_no_tokens() -> None:
    trace = build_trace("run-0", "x", _intent(), _graph(), _report())
    assert trace.outcome["prompt_tokens"] == 0
    assert trace.outcome["completion_tokens"] == 0
    assert trace.outcome["estimated_cost_usd"] == 0.0


def test_trace_metrics_move() -> None:
    from prometheus_client.registry import REGISTRY

    before = (
        REGISTRY.get_sample_value("msb_v3_queries_total", {"harness": "agentic", "event": "trace:recorded"})
        or 0.0
    )
    record_trace(build_trace("run-1", "x", _intent(), _graph(), _report()), audit_chain=_Audit())
    after = (
        REGISTRY.get_sample_value("msb_v3_queries_total", {"harness": "agentic", "event": "trace:recorded"})
        or 0.0
    )
    assert after == before + 1
