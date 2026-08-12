"""Tests for the agent executor (msb_v3.agent.executor)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from msb_v3.agent.dag import Task, TaskGraph  # noqa: E402
from msb_v3.agent.executor import execute_graph  # noqa: E402


class FakeProvider:
    """Scripted provider: results per tool name, optional fail-before-success
    counts and per-tool delays. Records every call it receives."""

    def __init__(
        self,
        script: Dict[str, Any],
        *,
        fail_counts: Dict[str, int] | None = None,
        delays: Dict[str, float] | None = None,
    ) -> None:
        self.script = script
        self.fail_counts = fail_counts or {}
        self.delays = delays or {}
        self.calls: list[dict] = []

    async def run_tool(self, name: str, *, task: Task, inputs: Dict[str, Any], session: str) -> Any:
        self.calls.append({"name": name, "task_id": task.task_id, "inputs": inputs, "session": session})
        if self.fail_counts.get(name, 0) > 0:
            self.fail_counts[name] -= 1
            raise ConnectionError("transient failure")
        delay = self.delays.get(name, 0.0)
        if delay:
            await asyncio.sleep(delay)
        return self.script[name]


def _chain(*tool_names: str, timeouts: Dict[str, float] | None = None) -> TaskGraph:
    """A chain graph: task per tool, parents wired in order."""
    tasks: list[Task] = []
    prev: str | None = None
    for i, tool in enumerate(tool_names):
        tid = f"t{i}"
        tasks.append(
            Task(
                task_id=tid,
                goal=f"goal {tid}",
                parent_id=prev,
                inputs=(({"from": prev, "kind": "output"},) if prev else ()),
                tools=(tool,),
                required_capabilities=(tool,),
                verification_method="none",
                timeout_s=(timeouts or {}).get(tid, 60.0),
                retry_policy="retry:0",
            )
        )
        prev = tid
    return TaskGraph(goal="chain", tasks=tuple(tasks))


@pytest.mark.asyncio
async def test_chain_executes_in_order_and_passes_inputs() -> None:
    graph = _chain("a", "b")
    provider = FakeProvider({"a": "A-out", "b": "B-out"})
    report = await execute_graph(graph, provider, session="s1")

    assert report.ok is True
    assert [c["task_id"] for c in provider.calls] == ["t0", "t1"]
    assert provider.calls[0]["session"] == "s1"
    # t1 received t0's output as its declared input
    t1_inputs = provider.calls[1]["inputs"]
    assert t1_inputs["t0"] == {"a": "A-out"}
    assert report.result_of("t1").output == {"b": "B-out"}


@pytest.mark.asyncio
async def test_retry_policy_recovers_after_transient_failures() -> None:
    graph = TaskGraph(
        goal="g",
        tasks=(
            Task(task_id="x", goal="gx", tools=("a",), verification_method="none", retry_policy="retry:2"),
        ),
    )
    provider = FakeProvider({"a": "ok"}, fail_counts={"a": 2})  # fails twice, then succeeds
    report = await execute_graph(graph, provider)
    assert report.ok is True
    assert report.result_of("x").attempts == 3


@pytest.mark.asyncio
async def test_retries_exhausted_fails_task_and_skips_downstream() -> None:
    graph = _chain("a", "b")
    # "a" always fails (fail count higher than any retries on t0, retry:0)
    provider = FakeProvider({"a": "x", "b": "y"}, fail_counts={"a": 5})
    report = await execute_graph(graph, provider)
    assert report.ok is False
    assert report.result_of("t0").ok is False
    assert report.skipped == ("t1",)
    assert len(provider.calls) == 1  # t1 never ran


@pytest.mark.asyncio
async def test_timeout_fails_task() -> None:
    graph = TaskGraph(
        goal="g",
        tasks=(
            Task(task_id="slow", goal="gs", tools=("a",), verification_method="none", timeout_s=0.05),
        ),
    )
    provider = FakeProvider({"a": "late"}, delays={"a": 0.5})
    report = await execute_graph(graph, provider)
    assert report.ok is False
    result = report.result_of("slow")
    assert "timed out" in (result.error or "")


@pytest.mark.asyncio
async def test_verifier_hook_can_fail_a_task() -> None:
    graph = _chain("a")
    provider = FakeProvider({"a": "A-out"})

    def strict_verify(task: Task, output: Dict[str, Any]) -> Dict[str, Any]:
        if task.task_id == "t0" and output.get("a") != "expected":
            return {"ok": False, "detail": "wrong value"}
        return {"ok": True, "detail": "ok"}

    report = await execute_graph(graph, provider, verify=strict_verify)
    assert report.ok is False
    assert report.result_of("t0").verification == {"ok": False, "detail": "wrong value"}


@pytest.mark.asyncio
async def test_deterministic_execution_order() -> None:
    graph = _chain("a", "b", "c")
    p1 = FakeProvider({"a": 1, "b": 2, "c": 3})
    p2 = FakeProvider({"a": 1, "b": 2, "c": 3})
    r1 = await execute_graph(graph, p1)
    r2 = await execute_graph(graph, p2)
    assert [c["task_id"] for c in p1.calls] == [c["task_id"] for c in p2.calls]
    assert [r.output for r in r1.results] == [r.output for r in r2.results]


@pytest.mark.asyncio
async def test_cycle_graph_returns_not_executable() -> None:
    graph = TaskGraph(
        goal="g",
        tasks=(
            Task(task_id="a", goal="ga", parent_id="b", tools=("a",)),
            Task(task_id="b", goal="gb", parent_id="a", tools=("b",)),
        ),
    )
    provider = FakeProvider({"a": 1, "b": 2})
    report = await execute_graph(graph, provider)
    assert report.ok is False
    assert "not executable" in (report.error or "")


@pytest.mark.asyncio
async def test_metrics_move_on_completion_and_failure() -> None:
    from prometheus_client.registry import REGISTRY

    def count(event: str) -> float:
        return (
            REGISTRY.get_sample_value("msb_v3_queries_total", {"harness": "agentic", "event": event})
            or 0.0
        )

    before_ok = count("exec:completed")
    before_fail = count("exec:failed")

    await execute_graph(_chain("a"), FakeProvider({"a": 1}))
    await execute_graph(_chain("a"), FakeProvider({"a": 1}, fail_counts={"a": 5}))

    assert count("exec:completed") == before_ok + 1
    assert count("exec:failed") == before_fail + 1
