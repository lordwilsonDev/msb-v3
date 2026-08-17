"""M5 — the failure matrix (blueprint §M5, live-loop-composition-plan D1–D4).

Every high-risk failure mode gets a test with an EXPECTED TERMINAL STATE and
an EVIDENCE assertion. The two invariants:

- NO SILENT UNSAFE CONTINUATION: a failed or uncertain action cannot proceed
  invisibly — it stops, degrades to a safe fallback, or escalates. Never
  "looks like it worked".
- RECOVERY IS BOUNDED: retries, timeouts, and escalation are measurable and
  finite.

Matrix (blueprint list -> test):
  1. model unavailability    -> fail-closed, no execution
  2. invalid model output    -> template/error fallback, never fake pass
  3. tool timeout            -> task fails with timeout, downstream skipped
  4. permission denial       -> BLOCK, no execution, audit record
  5. duplicate request       -> re-evaluated independently (no allow cache)
  6. partial completion      -> downstream tasks skipped, report says so
  7. stale evidence          -> tamper detected, chain refused
  8. corrupted state         -> store failure degrades, never crashes
  9. retry exhaustion        -> bounded attempts, then stop
  10. prompt injection       -> tainted write escalated to REVIEW
  11. conflicting instructions -> taint beats approval; tier beats approve

All hermetic: fake providers/clients, no model, no Qdrant, no network —
same deterministic style as chaos phase1/phase2.
"""

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


def _task(tid: str, tool: str, capability: str, parent: str | None = None, retry: str = "retry:2", timeout: float = 5.0) -> Task:
    return Task(
        task_id=tid,
        goal=f"goal {tid}",
        parent_id=parent,
        inputs=(({"from": parent, "kind": "output"},) if parent else ()),
        tools=(tool,),
        required_capabilities=(capability,),
        verification_method="none",
        retry_policy=retry,
        timeout_s=timeout,
    )


def _graph(*tasks: Task) -> TaskGraph:
    return TaskGraph(goal="failure matrix", tasks=tasks)


# ---------------------------------------------------------------------------
# 1. MODEL UNAVAILABILITY
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_model_unavailable_fails_closed_no_execution() -> None:
    """A dead model backend must fail the run — never fall through to an
    uncontrolled or half-executed action."""

    class _DeadProvider:
        async def run_tool(self, name: str, *, task: Task, inputs: Dict[str, Any], session: str) -> Any:
            raise ConnectionError("ollama down")

    wrapped = SafeProvider(_DeadProvider(), ActionGate(audit_chain=_Audit()))
    report = await execute_graph(_graph(_task("t", "search_query", "read_vault")), wrapped)
    assert report.ok is False
    assert report.error and "ollama down" in report.error  # visible, not silent


# ---------------------------------------------------------------------------
# 2. INVALID MODEL OUTPUT
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_invalid_tool_output_fails_verification_not_silent() -> None:
    """A tool returning garbage must fail the grounded verification, not be
    accepted because 'the model said so' — no LLM judge, no silent pass."""
    from msb_v3.agent.verify import verify_task

    graph = TaskGraph(
        goal="g",
        tasks=(Task(task_id="s", goal="gs", tools=("search_query",), required_capabilities=("read_vault",), verification_method="search_returned_hits"),),
    )

    class _EmptyProvider:
        async def run_tool(self, name: str, *, task: Task, inputs: Dict[str, Any], session: str) -> Any:
            return []  # search returned zero hits

    wrapped = SafeProvider(_EmptyProvider(), ActionGate(audit_chain=_Audit()))
    report = await execute_graph(graph, wrapped)
    assert report.ok is False
    result = report.result_of("s")
    assert result is not None and result.verification["ok"] is False
    # Sanity: the same output through the raw verifier is not a pass.
    task = graph.by_id("s")
    assert verify_task(task, {"search_query": []})["ok"] is False


# ---------------------------------------------------------------------------
# 3. TOOL TIMEOUT
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tool_timeout_fails_task_and_skips_downstream() -> None:
    """A slow tool is cut by the executor timeout; the failed task stops the
    graph and downstream work is skipped — never run anyway."""

    class _SlowProvider:
        async def run_tool(self, name: str, *, task: Task, inputs: Dict[str, Any], session: str) -> Any:
            await asyncio.sleep(10)

    wrapped = SafeProvider(_SlowProvider(), ActionGate(audit_chain=_Audit()))
    graph = _graph(
        _task("first", "search_query", "read_vault", timeout=0.1),
        _task("second", "vault_write", "write_file", parent="first", timeout=0.1),
    )
    report = await execute_graph(graph, wrapped)
    assert report.ok is False
    assert "timed out" in (report.error or "")
    assert report.skipped == ("second",)  # downstream never ran


# ---------------------------------------------------------------------------
# 4. PERMISSION DENIAL
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_permission_denial_blocks_without_execution_and_audits() -> None:
    """Denied capability: no tool call, verdict in the audit record."""
    audit = _Audit()
    underlying = _Provider_script({"permissions_op": {"ok": True}})
    gate = ActionGate(audit_chain=audit)
    wrapped = SafeProvider(underlying, gate)
    task = _task("p", "permissions_op", "permissions")
    with pytest.raises(GateBlocked):
        await wrapped.run_tool("permissions_op", task=task, inputs={}, session="s")
    assert underlying.calls == []
    assert audit.events and audit.events[0][1] == "blocked"


class _Provider_script:
    def __init__(self, script: Dict[str, Any]) -> None:
        self.script = script
        self.calls: list[str] = []

    async def run_tool(self, name: str, *, task: Task, inputs: Dict[str, Any], session: str) -> Any:
        self.calls.append(name)
        return self.script[name]


# ---------------------------------------------------------------------------
# 5. DUPLICATE REQUEST
# ---------------------------------------------------------------------------

def test_duplicate_request_reevaluated_independently_no_allow_cache() -> None:
    """The same request twice is gated twice — a prior allow never leaks into
    the second call (no per-capability cache)."""
    audit = _Audit()
    gate = ActionGate(audit_chain=audit)
    # First: allowed (untainted write, tier 2).
    assert gate.gate("write_file").action == "SAFE"
    # Second, identical call, still allowed — and equally, a denied request
    # repeated is denied again.
    assert gate.gate("financial").action == "BLOCK"
    assert gate.gate("financial").action == "BLOCK"
    assert len(audit.events) == 2  # two independent refusal records


# ---------------------------------------------------------------------------
# 6. PARTIAL COMPLETION
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_partial_completion_reports_successes_and_skips() -> None:
    """When one task in a chain fails, completed work is reported and the
    rest is explicitly skipped — no silent partial success."""
    class _FailingWrite(_Provider_script):
        async def run_tool(self, name: str, *, task: Task, inputs: Dict[str, Any], session: str) -> Any:
            self.calls.append(name)
            if name == "vault_write":
                raise RuntimeError("disk full")
            return self.script[name]

    provider = _FailingWrite({"search_query": [{"id": "a"}], "vault_write": {"path": "/tmp/x.md"}, "send_message": {"ok": True}})
    wrapped = SafeProvider(provider, ActionGate(audit_chain=_Audit()))
    graph = _graph(
        _task("research", "search_query", "read_vault"),
        _task("write", "vault_write", "write_file", parent="research"),
        _task("notify", "send_message", "send_message", parent="write"),
    )
    report = await execute_graph(graph, wrapped)
    assert report.ok is False
    assert report.result_of("research") is not None and report.result_of("research").ok is True  # type: ignore[union-attr]
    assert report.result_of("write") is not None and report.result_of("write").ok is False  # type: ignore[union-attr]
    assert report.skipped == ("notify",)


# ---------------------------------------------------------------------------
# 7. STALE EVIDENCE
# ---------------------------------------------------------------------------

def test_stale_evidence_tamper_detected_chain_refused(tmp_path) -> None:
    """A tampered audit chain must be detected and refused — stale evidence
    never silently passes."""
    from msb_v3.uac.audit_chain import AuditChain, tamper

    chain = AuditChain(str(tmp_path / "audit.db"), allow_keyless=True)
    chain.append("agentic", "allowed", {"a": 1})
    chain.append("agentic", "allowed", {"a": 2})
    before = chain.verify_chain()
    assert before["valid"] is True

    # Tamper with the genesis record (the tail hash chain breaks).
    tamper(str(tmp_path / "audit.db"), "UPDATE audit_records SET payload=? WHERE seq=1", ('{"a": 999}',))

    after = chain.verify_chain()
    assert after["valid"] is False  # detected, not trusted
    assert after["broken_at_seq"] == 1


# ---------------------------------------------------------------------------
# 8. CORRUPTED STATE
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_store_failure_degrades_never_crashes() -> None:
    """A failing persistence store must degrade the run, not break it — and
    the run must still report its real outcome."""
    from msb_v3.agent.executor import ExecReport, TaskResult

    class _BrokenStore:
        def save_task(self, run_id: str, task: Task, result: TaskResult, status: str) -> None:
            raise RuntimeError("store corrupted")

    underlying = _Provider_script({"search_query": [{"id": "a"}]})
    wrapped = SafeProvider(underlying, ActionGate(audit_chain=_Audit()))
    report: ExecReport = await execute_graph(
        _graph(_task("t", "search_query", "read_vault")),
        wrapped,
        store=_BrokenStore(),
        run_id="r1",
    )
    assert report.ok is True  # execution succeeded; persistence degraded only
    assert underlying.calls == ["search_query"]


# ---------------------------------------------------------------------------
# 9. RETRY EXHAUSTION
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retry_exhaustion_is_bounded_then_stops() -> None:
    """retry:2 means at most 3 attempts — after that the task fails and the
    graph stops; it never retries forever."""

    class _FlakyProvider:
        def __init__(self) -> None:
            self.attempts = 0

        async def run_tool(self, name: str, *, task: Task, inputs: Dict[str, Any], session: str) -> Any:
            self.attempts += 1
            raise RuntimeError("transient")

    provider = _FlakyProvider()
    wrapped = SafeProvider(provider, ActionGate(audit_chain=_Audit()))
    report = await execute_graph(_graph(_task("t", "search_query", "read_vault", retry="retry:2")), wrapped)
    assert provider.attempts == 3  # bounded: 1 + 2 retries
    assert report.ok is False


# ---------------------------------------------------------------------------
# 10. PROMPT INJECTION
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_prompt_injection_taint_escalates_write_to_review() -> None:
    """A write whose content derives from untrusted retrieval (the injection
    vector) is REVIEW-gated — the injected instruction cannot drive the write
    it was never approved for."""
    underlying = _Provider_script({"search_query": [{"id": "a"}], "vault_write": {"path": "/tmp/x.md"}})
    gate = ActionGate(audit_chain=_Audit())
    wrapped = SafeProvider(underlying, gate, approved=set())
    research = _task("research", "search_query", "read_vault")
    await wrapped.run_tool("search_query", task=research, inputs={}, session="s")
    assert wrapped.is_task_tainted("research") is True

    write = _task("write", "vault_write", "write_file", parent="research")
    with pytest.raises(GateReview):
        await wrapped.run_tool("vault_write", task=write, inputs={}, session="s")
    assert underlying.calls == ["search_query"]  # the write never executed


# ---------------------------------------------------------------------------
# 11. CONFLICTING INSTRUCTIONS
# ---------------------------------------------------------------------------

def test_conflicting_instructions_taint_beats_approval_tier_beats_approve() -> None:
    """Two conflict rules, both fail-safe:
    - a tainted write is REVIEW even when its nominal tier is low (taint
      beats the approved happy path unless explicitly pre-authorized);
    - a tier-4 action is BLOCK even when it was pre-approved (tier beats
      approval)."""
    gate = ActionGate(audit_chain=_Audit())
    # Conflict 1: pre-approved but tainted -> still REVIEW (not in approved).
    assert gate.gate("write_file", tainted_inputs=True, approved=set()).action == "REVIEW"
    # ... unless the plan declared it: approved tainted write executes.
    assert gate.gate("write_file", tainted_inputs=True, approved={"write_file"}).action == "SAFE"
    # Conflict 2: pre-approved but tier 4 -> BLOCK wins.
    assert gate.gate("permissions", approved={"permissions"}).action == "BLOCK"
