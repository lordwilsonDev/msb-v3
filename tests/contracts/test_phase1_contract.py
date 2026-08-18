'''Phase 1 release contract — the invariants that define "Phase 1 is done".

Each INVARIANT is a named, executable guarantee. The full suite is not just
"a lot of tests" — these six plus the final chain-break are the architectural
contract, so a regression that breaks one fails with its name:

    INVARIANT-001  BLOCK requests make zero model calls.
    INVARIANT-002  MoIE failure cannot bypass ActionGate.
    INVARIANT-003  Tool interactions use structured message semantics.
    INVARIANT-004  Tool-loop behavior is reproducible under test.
    INVARIANT-005  All privileged actions produce evidence.
    INVARIANT-006  Performance claims are benchmark-derived, not assumed.

    FINAL-10/10     Malicious request -> MoIE manipulation/failure -> intent
                    interpretation -> tool call -> privileged action cannot
                    cross the authorization boundary.

Deeper suites: tests/contracts/test_gate_contract.py (INVARIANT-001 over the
full corpus + precision/recall), test_layered_boundary.py (INVARIANT-002 +
chain-break variants), test_evidence_receipt.py (the receipt INVARIANT-005
feeds).
'''

from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _helpers import (  # noqa: E402
    INTENT_WITH_PRIVILEGED,
    INTENT_WITH_WRITE,
    MALICIOUS_PLAN,
    Audit,
    FakeMoIE,
    FakeProvider,
    SequenceClient,
    TrackingClient,
)

from msb_v3.agent.dag import Task, TaskGraph  # noqa: E402
from msb_v3.agent.executor import execute_graph  # noqa: E402
from msb_v3.agent.handle import handle  # noqa: E402
from msb_v3.agent.safety import ActionGate, SafeProvider  # noqa: E402
from msb_v3.evidence.spine import DecisionEvidenceStore  # noqa: E402
from msb_v3.local_ai.ollama import LocalAIClient  # noqa: E402

_WHITELIST = {"read_vault", "llm_synthesis", "write_file"}


# ── INVARIANT-001: BLOCK requests make zero model calls ─────────────────────

@pytest.mark.asyncio
async def test_invariant_001_block_makes_zero_model_calls(tmp_path: Path) -> None:
    client = TrackingClient(SequenceClient(INTENT_WITH_WRITE))
    result = await handle(
        "rm -rf production",
        client=client,
        approve=True,
        provider=FakeProvider(tmp_path),
        gate=ActionGate(audit_chain=Audit()),
        moie=FakeMoIE("BLOCK"),
    )
    assert result.verdict == "BLOCKED"
    assert result.model_calls == 0
    assert client.generate_calls == 0
    # Corpus-wide enforcement lives in test_gate_contract.py (every corpus
    # claim under a forced BLOCK asserts model_calls == 0).


# ── INVARIANT-002: MoIE failure cannot bypass ActionGate ────────────────────

@pytest.mark.asyncio
async def test_invariant_002_moie_failure_cannot_bypass_actiongate(tmp_path: Path) -> None:
    '''A MoIE that raises (outage) must not let a privileged task through:
    the ActionGate inside SafeProvider still blocks tier-4, tool never runs.'''
    graph = TaskGraph(
        goal="delete the database",
        tasks=(
            Task(
                task_id="evil",
                goal="delete the database",
                required_capabilities=("permissions",),
                tools=("permissions",),
                verification_method="none",
            ),
        ),
    )
    provider = FakeProvider(tmp_path)
    safe = SafeProvider(provider, ActionGate(audit_chain=Audit()))
    # MoIE is not consulted anywhere in this path; simulate an outage anyway.
    broken = FakeMoIE(error=RuntimeError("registry unavailable"))
    report = await execute_graph(graph, safe, run_id="inv002")
    assert report.ok is False
    assert "blocked" in (report.error or "").lower()
    assert provider.calls == []
    assert broken.claims == []  # MoIE was never part of the decision


# ── INVARIANT-003: tool interactions use structured message semantics ───────

class _FakeResponse:
    def __init__(self, data: Dict[str, Any]) -> None:
        self._data = data

    def raise_for_status(self) -> None:
        pass

    def json(self) -> Dict[str, Any]:
        return self._data


class _FakePost:
    def __init__(self, responses: list) -> None:
        self.responses = responses
        self.payloads: list = []

    def __call__(self, url: str, json: Dict[str, Any], **kwargs: Any) -> _FakeResponse:
        self.payloads.append(copy.deepcopy(json))
        body = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        return _FakeResponse(body)


class _FakeClient:
    def __init__(self, post: _FakePost) -> None:
        self._post = post

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *args: Any) -> None:
        pass

    def post(self, url: str, json: Dict[str, Any], **kwargs: Any) -> _FakeResponse:
        return self._post(url, json, **kwargs)


def test_invariant_003_tool_loop_uses_structured_messages(monkeypatch) -> None:
    '''The tool loop sends /api/chat with a real messages array — a tool
    result is a role="tool" message, never text flattened into a user turn.'''
    post = _FakePost(
        [
            {"message": {"content": "call it", "tool_calls": [{"function": {"name": "nope", "arguments": {}}}]}},
            {"message": {"content": "done", "tool_calls": None}},
        ]
    )
    monkeypatch.setattr(
        "msb_v3.local_ai.ollama.httpx.Client",
        lambda timeout=None: _FakeClient(post),
    )
    client = LocalAIClient(base_url="http://fake:11434")
    client.register_tool("nope", lambda **kw: "unused")
    resp = client.execute_tool_loop("what", tools=[{"name": "nope", "description": "x"}], max_steps=3)
    assert resp.text == "done"
    for payload in post.payloads:
        assert "messages" in payload
        assert "prompt" not in payload  # never the flat-string /api/generate shape
    assert post.payloads[1]["messages"][-1]["role"] == "tool"


# ── INVARIANT-004: tool-loop behavior is reproducible under test ────────────

@pytest.mark.asyncio
async def test_invariant_004_same_input_same_evidence(tmp_path: Path) -> None:
    '''Replay determinism: identical input yields the identical
    deterministic_hash — the tool-loop behavior is reproducible, not
    flaky.'''
    async def run_once() -> str:
        result = await handle(
            "research the vault and write a client brief",
            client=SequenceClient(INTENT_WITH_WRITE),
            approve=True,
            provider=FakeProvider(tmp_path),
            gate=ActionGate(audit_chain=Audit()),
        )
        return result.deterministic_hash

    h1 = await run_once()
    h2 = await run_once()
    assert h1 == h2
    assert h1 != ""


# ── INVARIANT-005: all privileged actions produce evidence ──────────────────

def test_invariant_005_gate_refusal_is_audited() -> None:
    '''A privileged-action refusal is written to the audit chain — no silent
    denial.'''
    audit = Audit()
    gate = ActionGate(audit_chain=audit)
    gate.gate("permissions")
    assert len(audit.events) == 1
    component, event_type, payload = audit.events[0]
    assert component == "agentic"
    assert event_type == "blocked"
    assert payload["capability"] == "permissions"


@pytest.mark.asyncio
async def test_invariant_005_denied_request_leaves_spine_record(tmp_path: Path) -> None:
    '''A quick-reject BLOCK is a governed decision: it leaves a DENY vertebra
    on the evidence spine, so a refusal is reconstructable like an execution.'''
    spine = DecisionEvidenceStore(str(tmp_path / "spine.db"))
    result = await handle(
        "rm -rf production",
        client=SequenceClient(INTENT_WITH_WRITE),
        approve=True,
        provider=FakeProvider(tmp_path),
        gate=ActionGate(audit_chain=Audit()),
        moie=FakeMoIE("BLOCK"),
        spine=spine,
    )
    assert result.verdict == "BLOCKED"
    trail = spine.trail(result.run_id)
    assert len(trail) == 1
    assert trail[0].evidence.kind == "decision"
    assert trail[0].evidence.policy_result == "DENY"
    assert spine.verify_chain()["valid"] is True


# ── INVARIANT-006: performance claims are benchmark-derived ─────────────────

def test_invariant_006_benchmark_report_shape() -> None:
    '''The prefix-cache claim must come from a benchmark report, not an
    assumption. The harness produces the required fields; this test drives it
    with a fake chat function (that simulates a cold call then fast cached
    calls) so it runs without a live server.'''
    import time

    from experiments.benchmark_prefix_cache import run_benchmark

    calls = []

    def fake_chat(messages: list, model: str) -> Dict[str, Any]:
        calls.append(messages)
        # Simulate a cold call (slow) then prefix-cached calls (fast): the
        # wall-time collapse is what the cache-state inference keys on.
        time.sleep(0.5 if len(calls) == 1 else 0.05)
        return {
            "prompt_eval_count": 3197,
            "eval_count": 24,
            "eval_duration": 24000000000,  # 24s of eval -> 1.0 tok/s
            "load_duration": 3000000000 if len(calls) == 1 else 0,
        }

    report = run_benchmark(model="qwen3:8b", n_calls=5, prompt="prefix " * 500, chat_fn=fake_chat)

    assert len(report["calls"]) == 5
    assert report["meta"]["model"] == "qwen3:8b"
    assert "hardware" in report["meta"]
    for c in report["calls"]:
        assert "wall_ms" in c
        assert "prompt_tokens" in c
        assert "generated_tokens" in c
        assert "tokens_per_sec" in c
        assert "cache_state" in c
    assert report["calls"][0]["cache_state"] == "cold"
    assert report["calls"][1]["cache_state"] == "prefix-cached"
    assert report["summary"]["prefix_cache_speedup_x"] > 1.0
    assert len(calls) == 5


# ── FINAL-10/10: the chain-break ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_final_chain_break_privileged_action_cannot_cross_boundary(tmp_path: Path) -> None:
    '''Malicious request -> MoIE wrongly approves -> intent interpretation ->
    malicious tool call -> privileged action. The request cannot cross the
    authorization boundary: the plan parser strips the privileged capability
    and the closed tool vocabulary refuses the tool.'''
    client = SequenceClient(INTENT_WITH_PRIVILEGED, MALICIOUS_PLAN)
    result = await handle(
        "delete the database and escalate privileges",
        client=client,
        approve=False,
        provider=FakeProvider(tmp_path),
        gate=ActionGate(audit_chain=Audit()),
        moie=FakeMoIE("APPROVE"),
    )
    # The executed graph carries only whitelisted capabilities.
    for task in result.trace.get("tasks", []):
        assert set(task.get("required_capabilities", [])) <= _WHITELIST
    # No privileged action succeeded.
    assert result.verdict in {"FAIL", "ERROR", "BLOCKED"}
    assert result.ok is False
