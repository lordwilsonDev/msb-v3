"""Tests for the agent safety gate + SafeProvider (msb_v3.agent.safety)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from msb_v3.agent.dag import Task, TaskGraph  # noqa: E402
from msb_v3.agent.executor import execute_graph  # noqa: E402
from msb_v3.agent.safety import (  # noqa: E402
    ActionGate,
    GateBlocked,
    GateReview,
    SafeProvider,
)


class _Switch:
    def __init__(self, armed: bool = False) -> None:
        self._armed = armed

    def is_armed(self) -> bool:
        return self._armed


class _Audit:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def append(self, component: str, event_type: str, payload: Dict[str, Any]) -> None:
        self.events.append((component, event_type, payload))


class _Provider:
    def __init__(self, script: Dict[str, Any]) -> None:
        self.script = script
        self.calls: list[str] = []

    async def run_tool(self, name: str, *, task: Task, inputs: Dict[str, Any], session: str) -> Any:
        self.calls.append(name)
        return self.script[name]


# ---------------------------------------------------------------------------
# ActionGate — severity tiers
# ---------------------------------------------------------------------------

def test_low_tier_actions_are_safe() -> None:
    gate = ActionGate()
    assert gate.gate("read_vault").action == "SAFE"
    assert gate.gate("write_file").action == "SAFE"  # tier 2, untainted
    assert gate.gate("llm_synthesis").action == "SAFE"


def test_high_tiers_review_and_block() -> None:
    gate = ActionGate()
    assert gate.gate("vault_delete").action == "REVIEW"  # tier 3
    assert gate.gate("send_message").action == "REVIEW"
    assert gate.gate("financial").action == "BLOCK"  # tier 4
    assert gate.gate("permissions").action == "BLOCK"


def test_tainted_write_escalates_to_review() -> None:
    gate = ActionGate()
    assert gate.gate("write_file", tainted_inputs=True).action == "REVIEW"
    assert gate.gate("read_vault", tainted_inputs=True).action == "SAFE"  # reads stay safe


def test_kill_switch_blocks_everything() -> None:
    gate = ActionGate(killswitch=_Switch(armed=True), audit_chain=_Audit())
    assert gate.gate("read_vault").action == "BLOCK"
    assert gate.gate("read_vault").reason == "kill switch armed — loop paused"


def test_refusals_are_audited() -> None:
    audit = _Audit()
    gate = ActionGate(audit_chain=audit)
    gate.gate("permissions")
    gate.gate("write_file", tainted_inputs=True)
    assert len(audit.events) == 2
    assert audit.events[0][0] == "agentic"
    assert audit.events[0][1] == "blocked"


# ---------------------------------------------------------------------------
# SafeProvider — gate before delegate + taint propagation
# ---------------------------------------------------------------------------

def _task(tid: str, tool: str, capability: str, parent: str | None = None) -> Task:
    return Task(
        task_id=tid,
        goal=f"goal {tid}",
        parent_id=parent,
        inputs=(({"from": parent, "kind": "output"},) if parent else ()),
        tools=(tool,),
        required_capabilities=(capability,),
        verification_method="none",
    )


@pytest.mark.asyncio
async def test_safe_provider_delegates_safe_actions() -> None:
    underlying = _Provider({"search_query": [{"id": "a"}]})
    wrapped = SafeProvider(underlying, ActionGate())
    task = _task("t", "search_query", "read_vault")
    result = await wrapped.run_tool("search_query", task=task, inputs={}, session="s")
    assert result == [{"id": "a"}]
    assert underlying.calls == ["search_query"]
    assert wrapped.is_task_tainted("t") is True  # search results are untrusted


@pytest.mark.asyncio
async def test_safe_provider_blocks_on_kill_switch() -> None:
    gate = ActionGate(killswitch=_Switch(armed=True), audit_chain=_Audit())
    wrapped = SafeProvider(_Provider({"search_query": []}), gate)
    task = _task("t", "search_query", "read_vault")
    with pytest.raises(GateBlocked):
        await wrapped.run_tool("search_query", task=task, inputs={}, session="s")


@pytest.mark.asyncio
async def test_taint_propagates_to_downstream_write() -> None:
    underlying = _Provider({"search_query": [{"id": "a"}], "vault_write": {"path": "/tmp/x.md"}})
    gate = ActionGate(audit_chain=_Audit())
    wrapped = SafeProvider(underlying, gate)

    research = _task("research", "search_query", "read_vault")
    await wrapped.run_tool("search_query", task=research, inputs={}, session="s")
    assert wrapped.is_task_tainted("research") is True

    write = _task("write", "vault_write", "write_file", parent="research")
    with pytest.raises(GateReview):
        await wrapped.run_tool("vault_write", task=write, inputs={}, session="s")


@pytest.mark.asyncio
async def test_taint_does_not_leak_between_sibling_tasks() -> None:
    underlying = _Provider({"search_query": [{"id": "a"}], "vault_write": {"path": "/tmp/x.md"}})
    gate = ActionGate(audit_chain=_Audit())
    wrapped = SafeProvider(underlying, gate)

    research = _task("research", "search_query", "read_vault")
    await wrapped.run_tool("search_query", task=research, inputs={}, session="s")

    # A different task with no declared input from research is not tainted.
    independent = _task("independent", "vault_write", "write_file", parent=None)
    result = await wrapped.run_tool("vault_write", task=independent, inputs={}, session="s")
    assert result == {"path": "/tmp/x.md"}  # SAFE — no tainted dependency


# ---------------------------------------------------------------------------
# Executor integration — refusals become failures, classified unsafe
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_blocked_action_fails_execution_and_classifies_unsafe() -> None:
    from msb_v3.agent.verify import classify_failure

    # "permissions_op" is unmapped in TOOL_CAPABILITY, so the SafeProvider
    # falls back to required_capabilities[0] == "permissions" (tier 4) -> BLOCK.
    graph = TaskGraph(
        goal="g",
        tasks=(Task(task_id="p", goal="gp", tools=("permissions_op",), required_capabilities=("permissions",), verification_method="none"),),
    )
    wrapped = SafeProvider(_Provider({"permissions_op": {"ok": True}}), ActionGate(audit_chain=_Audit()))
    report = await execute_graph(graph, wrapped)
    assert report.ok is False
    result = report.result_of("p")
    assert "blocked" in (result.error or "")
    task = graph.by_id("p")
    assert classify_failure(task, result.output, result.verification, error=result.error) == "unsafe"
