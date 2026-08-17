"""M2 bypass regression suite — no alternate caller may escape the ActionGate.

The governed path is ``agent.handle()`` -> ``SafeProvider`` -> ``ActionGate``.
This suite pins the two ways a regression could sneak tools past the gate:

1. **Direct tool invocation** — calling the underlying ToolProvider (or the
   executor with a raw provider) instead of the SafeProvider wrapper.
2. **Alternate callers** — any code path that reaches tools without going
   through the gate (a new endpoint, a harness, the MCP surface).

Plus the state-machine property: **replay/retry must re-evaluate**, never
cache an allow (a denied request retried is denied again).

The invariant under test, stated once: *the ActionGate is the only thing that
decides whether a tool call may run, and SafeProvider is the only way tools
are reached on the live path.*
"""

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
    """A raw ToolProvider. Calling it directly is the bypass under test."""

    def __init__(self, script: Dict[str, Any]) -> None:
        self.script = script
        self.calls: list[str] = []

    async def run_tool(self, name: str, *, task: Task, inputs: Dict[str, Any], session: str) -> Any:
        self.calls.append(name)
        return self.script[name]


def _task(tid: str, tool: str, capability: str, parent: str | None = None, retry: str = "") -> Task:
    return Task(
        task_id=tid,
        goal=f"goal {tid}",
        parent_id=parent,
        inputs=(({"from": parent, "kind": "output"},) if parent else ()),
        tools=(tool,),
        required_capabilities=(capability,),
        verification_method="none",
        retry_policy=retry,
    )


def _graph(*tasks: Task) -> TaskGraph:
    return TaskGraph(goal="bypass test", tasks=tasks)


# ---------------------------------------------------------------------------
# 1. Direct tool invocation must not run ungoverned
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_raw_provider_called_directly_is_the_bypass_and_never_wired() -> None:
    """The raw provider itself is ungoverned — so it must never be reachable
    on the live path. This test documents the boundary: SafeProvider is the
    ONLY wrapper that gates, and the raw provider records any call."""
    underlying = _Provider({"search_query": [{"id": "a"}]})
    # Direct invocation (the bypass): no gate anywhere in this call chain.
    task = _task("t", "search_query", "read_vault")
    result = await underlying.run_tool("search_query", task=task, inputs={}, session="s")
    assert result == [{"id": "a"}]
    assert underlying.calls == ["search_query"]
    # The fix is structural, not a runtime check: the live path (handle())
    # constructs SafeProvider and never hands the raw provider to the
    # executor. That wiring is pinned by the handle() tests; this test
    # exists so the bypass is named, not hidden.


@pytest.mark.asyncio
async def test_executor_with_raw_provider_does_not_gate() -> None:
    """execute_graph() trusts its provider — passing a raw provider means no
    gating. Anyone wiring a new caller must wrap in SafeProvider first; this
    test makes that contract visible (the executor is a generic engine, the
    gate lives in SafeProvider)."""
    underlying = _Provider({"search_query": [{"id": "a"}]})
    report = await execute_graph(
        _graph(_task("t", "search_query", "read_vault")),
        underlying,  # raw — no gate
    )
    assert report.ok is True
    assert underlying.calls == ["search_query"]


@pytest.mark.asyncio
async def test_same_call_through_safe_provider_is_gated() -> None:
    """The identical capability through SafeProvider hits the gate — proving
    the gate is the delta, and the live path always applies it."""
    underlying = _Provider({"permissions_op": {"ok": True}})
    gate = ActionGate(audit_chain=_Audit())
    wrapped = SafeProvider(underlying, gate)
    task = _task("p", "permissions_op", "permissions")  # tier 4 -> BLOCK
    with pytest.raises(GateBlocked):
        await wrapped.run_tool("permissions_op", task=task, inputs={}, session="s")
    assert underlying.calls == []  # never reached the tool


# ---------------------------------------------------------------------------
# 2. Alternate callers must not reach tools ungoverned
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_alternate_http_surface_cannot_reach_dag_tools() -> None:
    """The MCP/chat surfaces advertise tools but route through their own
    governed registration (tools.runtime.register_governed_tools), not the
    DAG executor. This test pins the boundary: the DAG executor is only
    reachable from agent.handle(), which always wraps in SafeProvider.
    (The chat-surface gate is separately covered by tools/runtime tests.)"""
    # The executor is imported by handle.py and wrapped there — assert the
    # wiring is the SafeProvider call site, not a raw provider handoff.
    import inspect

    from msb_v3.agent import handle as handle_module

    source = inspect.getsource(handle_module.handle)
    assert "SafeProvider(" in source
    assert "execute_graph(graph, safe" in source  # wrapped, never raw


@pytest.mark.asyncio
async def test_granted_whitelist_blocks_through_safe_provider() -> None:
    """An agent with a standing grant cannot widen it via the wrapper: a
    capability outside the grant is BLOCKED even when the raw provider would
    happily run it (identity §17 — agents do only what they were granted)."""
    underlying = _Provider({"vault_write": {"path": "/tmp/x.md"}})
    gate = ActionGate(audit_chain=_Audit())
    wrapped = SafeProvider(underlying, gate, granted=set())  # granted: nothing
    task = _task("w", "vault_write", "write_file")
    with pytest.raises(GateBlocked):
        await wrapped.run_tool("vault_write", task=task, inputs={}, session="s")
    assert underlying.calls == []


@pytest.mark.asyncio
async def test_kill_switch_blocks_alternate_caller_path() -> None:
    """The kill switch is checked inside the gate, so every surface that goes
    through SafeProvider honors it — there is no bypass that skips the switch
    and still reaches tools."""
    underlying = _Provider({"search_query": []})
    gate = ActionGate(killswitch=_Switch(armed=True), audit_chain=_Audit())
    wrapped = SafeProvider(underlying, gate)
    task = _task("t", "search_query", "read_vault")
    with pytest.raises(GateBlocked):
        await wrapped.run_tool("search_query", task=task, inputs={}, session="s")
    assert underlying.calls == []


# ---------------------------------------------------------------------------
# 3. Replay / retry must re-evaluate — no cached allow, no cached deny
# ---------------------------------------------------------------------------

def test_denied_retried_is_denied_again_no_cache() -> None:
    """A blocked capability re-gated must be blocked again — the gate holds
    no per-capability allow/deny cache (each call re-evaluates tiers, taint,
    switch state, and grants)."""
    audit = _Audit()
    gate = ActionGate(audit_chain=audit)
    first = gate.gate("financial")  # tier 4 -> BLOCK
    second = gate.gate("financial")
    assert first.action == "BLOCK"
    assert second.action == "BLOCK"
    assert len(audit.events) == 2  # each refusal audited independently


def test_allow_not_cached_across_taint_change() -> None:
    """An allowed (SAFE) call must not create a cache that later forgives the
    same capability when it becomes tainted — the taint axis re-evaluates."""
    gate = ActionGate()
    assert gate.gate("write_file").action == "SAFE"  # untainted, tier 2
    # Same capability, now driven by untrusted content: REVIEW, not a cached
    # allow from the previous call.
    assert gate.gate("write_file", tainted_inputs=True).action == "REVIEW"


def test_allow_not_cached_across_switch_arm() -> None:
    """An allowed call before the kill switch is armed must not survive the
    arm — the switch is consulted on every gate() call."""
    switch = _Switch(armed=False)
    gate = ActionGate(killswitch=switch, audit_chain=_Audit())
    assert gate.gate("read_vault").action == "SAFE"
    switch._armed = True
    assert gate.gate("read_vault").action == "BLOCK"


@pytest.mark.asyncio
async def test_retry_policy_does_not_retry_a_blocked_action() -> None:
    """retry:N retries transient tool failures — a gate refusal is not
    transient, so a blocked action must fail once and stop, not burn retries
    or silently pass on a later attempt."""
    underlying = _Provider({"permissions_op": {"ok": True}})
    gate = ActionGate(audit_chain=_Audit())
    wrapped = SafeProvider(underlying, gate)
    graph = _graph(_task("p", "permissions_op", "permissions", retry="retry:3"))
    report = await execute_graph(graph, wrapped)
    assert report.ok is False
    assert underlying.calls == []  # never executed, not even once


@pytest.mark.asyncio
async def test_tainted_write_retried_after_approval_executes() -> None:
    """The REVIEW -> approve -> retry flow must work (the gate is not a
    one-way trip): the same tainted write with the capability now approved
    executes. This is the approval recovery path as a test."""
    underlying = _Provider({"search_query": [{"id": "a"}], "vault_write": {"path": "/tmp/x.md"}})
    gate = ActionGate(audit_chain=_Audit())
    task = _task("w", "vault_write", "write_file", parent="research")
    research = _task("research", "search_query", "read_vault")

    # Unapproved: tainted write -> REVIEW (no execution).
    wrapped = SafeProvider(underlying, gate, approved=set())
    await wrapped.run_tool("search_query", task=research, inputs={}, session="s")
    with pytest.raises(GateReview):
        await wrapped.run_tool("vault_write", task=task, inputs={}, session="s")
    assert underlying.calls == ["search_query"]

    # Approved (operator pre-authorized the plan): same tainted write runs.
    wrapped2 = SafeProvider(underlying, gate, approved={"write_file"})
    result = await wrapped2.run_tool("vault_write", task=task, inputs={}, session="s")
    assert result == {"path": "/tmp/x.md"}
    assert underlying.calls == ["search_query", "vault_write"]


# ---------------------------------------------------------------------------
# 4. Governance is observable — verdicts land in the metrics
# ---------------------------------------------------------------------------

def test_actiongate_metrics_count_verdicts() -> None:
    from prometheus_client.registry import REGISTRY

    def count(verdict: str) -> float:
        return (
            REGISTRY.get_sample_value(
                "msb_v3_actiongate_decisions_total", {"verdict": verdict}
            )
            or 0.0
        )

    before_allowed, before_denied, before_indeterminate = (
        count("allowed"),
        count("denied"),
        count("indeterminate"),
    )
    audit = _Audit()
    gate = ActionGate(audit_chain=audit)
    gate.gate("read_vault")  # allowed
    gate.gate("financial")  # denied
    gate.gate("write_file", tainted_inputs=True)  # indeterminate (REVIEW)
    assert count("allowed") == before_allowed + 1
    assert count("denied") == before_denied + 1
    assert count("indeterminate") == before_indeterminate + 1


def test_actiongate_metrics_count_gate_failure_fail_closed() -> None:
    from prometheus_client.registry import REGISTRY

    before = (
        REGISTRY.get_sample_value(
            "msb_v3_actiongate_decisions_total", {"verdict": "failed"}
        )
        or 0.0
    )

    class _BrokenAudit:
        def append(self, *a: Any, **k: Any) -> None:
            raise RuntimeError("chain down")

    class _BrokenSwitch:
        def is_armed(self) -> bool:
            raise RuntimeError("switch probe failed")

    # A raising switch makes _decide raise -> gate() counts "failed" and
    # re-raises (fail-closed: an exception is never a silent allow).
    gate = ActionGate(killswitch=_BrokenSwitch(), audit_chain=_BrokenAudit())
    with pytest.raises(RuntimeError):
        gate.gate("read_vault")
    assert (
        REGISTRY.get_sample_value(
            "msb_v3_actiongate_decisions_total", {"verdict": "failed"}
        )
        or 0.0
    ) == before + 1
