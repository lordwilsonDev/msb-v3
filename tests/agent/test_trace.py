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
