"""Tests for the runtime store — queryable Task/Trace persistence (Phase 0, D1)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from msb_v3.agent.dag import Task, TaskGraph  # noqa: E402
from msb_v3.agent.executor import ExecReport, TaskResult, execute_graph  # noqa: E402
from msb_v3.agent.trace import AgentTrace, build_trace, record_trace  # noqa: E402
from msb_v3.runtime.store import RuntimeStore  # noqa: E402


class _Audit:
    def append(self, component: str, event_type: str, payload: dict) -> None:
        pass


def _store(tmp_path) -> RuntimeStore:
    return RuntimeStore(db_path=str(tmp_path / "runtime.db"))


def _trace(run_id: str = "run-1") -> AgentTrace:
    graph = TaskGraph(
        goal="g",
        tasks=(
            Task(task_id="research", goal="gr", verification_method="search_returned_hits"),
            Task(task_id="synthesize", goal="gs", parent_id="research", verification_method="synthesis_nonempty"),
        ),
    )
    report = ExecReport(
        ok=True,
        goal="g",
        results=(
            TaskResult(task_id="research", ok=True, output={"search_query": [{"id": "a"}]},
                       verification={"ok": True, "detail": "1 hits"}, latency_s=0.1, attempts=1),
            TaskResult(task_id="synthesize", ok=True, output={"chat": "brief"},
                       verification={"ok": True, "detail": "5 chars"}, latency_s=0.2, attempts=1),
        ),
    )
    intent = _intent()
    return build_trace(run_id, "research the vault", intent, graph, report)


def _intent():
    from msb_v3.agent.intent import Intent

    return Intent(request="research the vault", goals=("research the vault",), permissions=("read_vault",), source="llm")


def test_save_and_get_trace_roundtrip(tmp_path) -> None:
    store = _store(tmp_path)
    trace = _trace("run-42")
    store.save_trace(trace)

    got = store.get_trace("run-42")
    assert got is not None
    assert got["run_id"] == "run-42"
    assert got["request"] == "research the vault"
    assert got["verdict"] == "PASS"
    assert got["deterministic_hash"] == trace.deterministic_hash
    assert [t["task_id"] for t in got["tasks"]] == ["research", "synthesize"]
    assert got["execution"][0]["verification"]["ok"] is True


def test_missing_trace_returns_none(tmp_path) -> None:
    assert _store(tmp_path).get_trace("nope") is None


def test_list_traces_newest_first(tmp_path) -> None:
    store = _store(tmp_path)
    store.save_trace(_trace("run-1"))
    store.save_trace(_trace("run-2"))
    rows = store.list_traces(limit=10)
    assert len(rows) == 2
    # newest created_ts first — run-2 was saved later
    assert rows[0]["run_id"] == "run-2"
    assert "deterministic_hash" in rows[0]


def test_latest_deterministic_hash(tmp_path) -> None:
    store = _store(tmp_path)
    store.save_trace(_trace("run-1"))
    assert store.latest_deterministic_hash("run-1") == _trace("run-1").deterministic_hash
    assert store.latest_deterministic_hash("missing") is None


def test_executor_persists_task_rows(tmp_path) -> None:
    import asyncio

    from msb_v3.agent.dag import Task, TaskGraph

    store = _store(tmp_path)

    async def run_tool(name, *, task, inputs, session):
        return "ok" if name == "search_query" else "brief"

    class _Prov:
        async def run_tool(self, name, *, task, inputs, session):
            return await run_tool(name, task=task, inputs=inputs, session=session)

    graph = TaskGraph(
        goal="g",
        tasks=(
            Task(task_id="research", goal="gr", tools=("search_query",), verification_method="none"),
            Task(task_id="synthesize", goal="gs", parent_id="research", tools=("chat",),
                 inputs=({"from": "research"},), verification_method="none"),
        ),
    )
    report = asyncio.run(execute_graph(graph, _Prov(), store=store, run_id="run-x"))
    assert report.ok

    tasks = store.get_tasks("run-x")
    assert len(tasks) == 2
    by_id = {t["task_id"]: t for t in tasks}
    assert by_id["research"]["status"] == "ok"
    assert by_id["research"]["verification_method"] == "none"
    assert by_id["synthesize"]["parent_id"] == "research"
    assert by_id["synthesize"]["attempts"] == 1


def test_record_trace_persists_trace_row(tmp_path) -> None:
    store = _store(tmp_path)
    trace = _trace("run-p")
    record_trace(trace, audit_chain=_Audit(), store=store)

    got = store.get_trace("run-p")
    assert got is not None
    assert got["verdict"] == "PASS"
    assert got["deterministic_hash"] == trace.deterministic_hash


def test_record_trace_store_failure_does_not_break_run(tmp_path) -> None:
    """I7: record_trace with a failing store logs and continues — the chain
    events still fire and the run verdict is unchanged (best-effort projection)."""
    from msb_v3.agent.trace import record_trace

    class _BrokenStore:
        def save_trace(self, trace) -> None:
            raise RuntimeError("disk full")

    events: list = []

    class _Audit:
        def append(self, component, event_type, payload) -> None:
            events.append((component, event_type, payload))

    trace = _trace("run-br")
    record_trace(trace, audit_chain=_Audit(), store=_BrokenStore())  # must NOT raise

    assert len(events) == 4  # chain events still written
    assert events[3][2]["verdict"] == "PASS"


def test_store_failure_does_not_break_executor(tmp_path) -> None:
    """I7 note: store persistence is best-effort — a failing store must not
    break the run. The run completes and reports ok; rows are simply absent."""
    import asyncio

    from msb_v3.agent.dag import Task, TaskGraph

    class _BrokenStore:
        def save_task(self, *args, **kwargs) -> None:
            raise RuntimeError("disk full")

    class _Prov:
        async def run_tool(self, name, *, task, inputs, session):
            return "ok"

    graph = TaskGraph(goal="g", tasks=(Task(task_id="t", goal="g", tools=("search_query",), verification_method="none"),))
    report = asyncio.run(execute_graph(graph, _Prov(), store=_BrokenStore(), run_id="run-b"))
    assert report.ok


def test_failed_run_marks_skipped_tasks(tmp_path) -> None:
    import asyncio

    from msb_v3.agent.dag import Task, TaskGraph

    store = _store(tmp_path)

    class _FailProv:
        async def run_tool(self, name, *, task, inputs, session):
            raise RuntimeError("boom")

    graph = TaskGraph(
        goal="g",
        tasks=(
            Task(task_id="a", goal="a", tools=("x",), verification_method="none"),
            Task(task_id="b", goal="b", parent_id="a", tools=("y",), verification_method="none"),
        ),
    )
    report = asyncio.run(execute_graph(graph, _FailProv(), store=store, run_id="run-f"))
    assert not report.ok
    assert report.skipped == ("b",)

    tasks = {t["task_id"]: t for t in store.get_tasks("run-f")}
    assert tasks["a"]["status"] == "failed"
    assert tasks["b"]["status"] == "skipped"
