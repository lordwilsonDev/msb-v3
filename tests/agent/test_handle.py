"""Slice acceptance tests — the Handle-this loop end-to-end (T1.7)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from msb_v3.agent.bridge_provider import (  # noqa: E402
    BridgeProvider,
    _extract_brief,
    _format_sources,
    _slug,
)
from msb_v3.agent.handle import handle  # noqa: E402
from msb_v3.agent.safety import ActionGate  # noqa: E402


class _Resp:
    def __init__(self, text: str) -> None:
        self.text = text
        self.model = "fake"
        self.latency_s = 0.0
        self.tool_calls = []


class SequenceClient:
    """Returns texts in order (intent JSON first, then plan garbage -> the
    template planner, which honors the intent's permissions)."""

    def __init__(self, *texts: str) -> None:
        self._texts = list(texts)

    def generate(self, prompt, *, system=None, tools=None, temperature=0.2, max_tokens=2048):
        text = self._texts.pop(0) if self._texts else "garbage"
        return _Resp(text)


_INTENT_WITH_WRITE = (
    '{"goals": ["research the vault"], "constraints": [], '
    '"permissions": ["read_vault", "write_file"], "privacy": true, "domain": "client-brief"}'
)


class _Audit:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def append(self, component: str, event_type: str, payload: Dict[str, Any]) -> None:
        self.events.append((component, event_type, payload))


class _Switch:
    def __init__(self, armed: bool = False) -> None:
        self._armed = armed

    def is_armed(self) -> bool:
        return self._armed


class FakeProvider:
    def __init__(self, tmp: Path) -> None:
        self._tmp = tmp
        self.calls: list[str] = []

    async def run_tool(self, name: str, *, task, inputs: Dict[str, Any], session: str) -> Any:
        self.calls.append(name)
        if name == "search_query":
            return [{"text": "source one", "source": "vault/a.md"}]
        if name == "chat":
            return "The client-ready brief."
        if name == "vault_write":
            path = self._tmp / "brief.md"
            path.write_text("# Brief\n\nbrief\n")  # Phase 1: heading required
            return {"path": str(path), "heading": "# Brief"}
        raise ValueError(name)


@pytest.mark.asyncio
async def test_happy_path_with_approval_passes_and_records_evidence(tmp_path: Path) -> None:
    client = SequenceClient(_INTENT_WITH_WRITE)
    provider = FakeProvider(tmp_path)
    gate = ActionGate(audit_chain=_Audit())

    result = await handle(
        "research the vault and write a client brief",
        client=client,
        approve=True,
        provider=provider,
        gate=gate,
    )

    assert result.ok is True
    assert result.verdict == "PASS"
    assert result.run_id.startswith("dbb-")
    assert result.deterministic_hash
    assert result.trace["graph_source"] == "template"
    assert result.trace["verdict"] == "PASS"
    assert [t["task_id"] for t in result.trace["tasks"]] == ["research", "synthesize", "write"]
    assert provider.calls == ["search_query", "chat", "vault_write"]


@pytest.mark.asyncio
async def test_replay_determinism_same_input_same_hash(tmp_path: Path) -> None:
    async def run_once() -> str:
        result = await handle(
            "research the vault and write a client brief",
            client=SequenceClient(_INTENT_WITH_WRITE),
            approve=True,
            provider=FakeProvider(tmp_path),
            gate=ActionGate(audit_chain=_Audit()),
        )
        return result.deterministic_hash

    h1 = await run_once()
    h2 = await run_once()
    assert h1 == h2
    assert h1 != ""


@pytest.mark.asyncio
async def test_tainted_write_halts_without_approval(tmp_path: Path) -> None:
    client = SequenceClient(_INTENT_WITH_WRITE)
    provider = FakeProvider(tmp_path)
    gate = ActionGate(audit_chain=_Audit())

    result = await handle(
        "research the vault and write a client brief",
        client=client,
        approve=False,  # no pre-authorization — the tainted write must stop
        provider=provider,
        gate=gate,
    )

    assert result.ok is False
    assert result.verdict == "FAIL"
    assert "review required" in (result.error or "")
    assert provider.calls == ["search_query", "chat"]  # the write never ran


@pytest.mark.asyncio
async def test_kill_switch_stops_the_loop(tmp_path: Path) -> None:
    client = SequenceClient(_INTENT_WITH_WRITE)
    gate = ActionGate(killswitch=_Switch(armed=True), audit_chain=_Audit())

    result = await handle(
        "research the vault and write a client brief",
        client=client,
        approve=True,
        provider=FakeProvider(tmp_path),
        gate=gate,
    )

    assert result.ok is False
    assert "kill switch" in (result.error or "")


@pytest.mark.asyncio
async def test_empty_request_is_error() -> None:
    result = await handle("   ")
    assert result.ok is False
    assert result.verdict == "ERROR"


@pytest.mark.asyncio
async def test_handle_emits_spine_vertebrae(tmp_path: Path) -> None:
    """Phase 2.2: the handle slice emits the decision → execution →
    verification causal chain onto the Evidence Spine when a spine is
    injected, with every vertebra linked back to the decision."""
    from msb_v3.evidence.spine import DecisionEvidenceStore

    spine = DecisionEvidenceStore(str(tmp_path / "spine.db"))
    client = SequenceClient(_INTENT_WITH_WRITE)
    provider = FakeProvider(tmp_path)
    gate = ActionGate(audit_chain=_Audit())

    result = await handle(
        "research the vault and write a client brief",
        client=client,
        approve=True,
        provider=provider,
        gate=gate,
        spine=spine,
    )

    assert result.ok is True
    trail = spine.trail(result.run_id)
    assert [r.evidence.kind for r in trail] == ["decision", "execution", "verification"]
    decision, execution, verification = trail
    assert decision.evidence.policy_result == "ALLOW"
    assert decision.evidence.capability_requested == ("read_vault", "write_file")
    assert decision.evidence.capability_granted == ("read_vault", "write_file")
    # every vertebra links back to the authorizing decision
    assert execution.evidence.parent_decision_id == decision.decision_id
    assert verification.evidence.parent_decision_id == decision.decision_id
    # verification vertebra carries the deterministic hash
    assert verification.evidence.verification_id == result.deterministic_hash
    assert spine.verify_chain()["valid"] is True


# ---------------------------------------------------------------------------
# BridgeProvider — the real tool wiring (write path is hermetic)
# ---------------------------------------------------------------------------

def test_bridge_provider_write_creates_file_with_heading(tmp_path: Path) -> None:
    """Phase 1 canonical task: the vault note leads with a # heading so the
    grounded file_written_with_heading check can verify it."""
    provider = BridgeProvider(output_dir=tmp_path)
    task = type("T", (), {"goal": "write a brief about sovereign architecture"})()

    async def run() -> dict:
        return await provider.run_tool(
            "vault_write",
            task=task,
            inputs={"synthesize": {"chat": "The client-ready brief."}},
            session="s",
        )

    result = asyncio_run(run())
    path = Path(result["path"])
    assert path.exists()
    text = path.read_text()
    assert text.strip().startswith("# ")
    assert "The client-ready brief." in text
    assert result["heading"].startswith("# ")
    assert path.name.startswith(_slug(task.goal))


def test_bridge_provider_synthesize_builds_budgeted_context_with_ledger(tmp_path: Path) -> None:
    """Phase 2: synthesis assembles retrieval hits under a hard token budget
    and returns the eviction ledger so the trace can show what fit and what
    was evicted (inversion omission #5)."""
    # Budget sized so ONE 300-char hit fits comfortably but two never do:
    # each hit is ~83 tokens (300 chars + label), system+query ~40, headers
    # ~4 — so 200 fits one hit and forces the second out.
    provider = BridgeProvider(output_dir=tmp_path, context_budget_tokens=200)
    task = type("T", (), {"goal": "write a brief about sovereign architecture"})()
    inputs = {
        "research": {
            "search_query": [
                {"id": "a", "score": 0.9, "text": "hit one " + "x" * 300, "source": "a.md"},
                {"id": "b", "score": 0.5, "text": "hit two " + "y" * 300, "source": "b.md"},
            ]
        }
    }

    # The bridge's client is a real local client unless injected — inject a
    # stub so no model is contacted. The local tier goes through ChatHarness,
    # so the stub must satisfy the harness's execute_tool_loop interface.
    class _StubClient:
        def execute_tool_loop(self, query, *, system=None, tools=None, max_steps=4, max_tokens=2048):
            from msb_v3.local_ai.ollama import LocalAIResponse

            return LocalAIResponse(
                text="the brief", model="stub", latency_s=0.0,
                prompt_tokens=10, completion_tokens=5,
            )

    provider._client = _StubClient()

    async def run() -> dict:
        return await provider.run_tool("chat", task=task, inputs=inputs, session="s")

    result = asyncio_run(run())
    assert result["text"] == "the brief"
    assert result["prompt_tokens"] == 10
    assert result["completion_tokens"] == 5
    ledger = result.get("context_ledger")
    assert ledger is not None
    assert ledger["budget_tokens"] == 200
    # Two 300-char hits cannot both fit — the highest-score one survives,
    # the other is evicted. The hard invariant always holds.
    assert ledger["included_matches"] == 1
    assert ledger["evicted_matches"] == 1
    assert ledger["total_tokens"] <= ledger["budget_tokens"]


def test_bridge_provider_synthesize_ledger_records_small_context(tmp_path: Path) -> None:
    """A small context fits fully: no eviction, ledger still present."""
    provider = BridgeProvider(output_dir=tmp_path, context_budget_tokens=1000)
    task = type("T", (), {"goal": "write a short brief"})()
    inputs = {"research": {"search_query": [{"id": "a", "score": 0.9, "text": "small hit", "source": "a.md"}]}}

    class _StubClient:
        def execute_tool_loop(self, query, *, system=None, tools=None, max_steps=4, max_tokens=2048):
            from msb_v3.local_ai.ollama import LocalAIResponse

            return LocalAIResponse(text="ok", model="stub", latency_s=0.0)

    provider._client = _StubClient()

    async def run() -> dict:
        return await provider.run_tool("chat", task=task, inputs=inputs, session="s")

    result = asyncio_run(run())
    ledger = result["context_ledger"]
    assert ledger["included_matches"] == 1
    assert ledger["evicted_matches"] == 0
    assert ledger["total_tokens"] <= ledger["budget_tokens"]


def test_bridge_provider_format_sources_and_extract_brief() -> None:
    inputs = {"research": {"search_query": [{"text": "hit one", "source": "a.md"}, {"text": "hit two", "source": "b.md"}]}}
    formatted = _format_sources(inputs)
    assert "hit one" in formatted and "hit two" in formatted
    assert _extract_brief({"synthesize": {"chat": "  the brief  "}}) == "the brief"
    assert _extract_brief({}) == ""


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)
