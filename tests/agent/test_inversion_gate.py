"""MoIE §25 integration at the external-agent delegation boundary.

The worker is never started before inversion completes. BLOCK and failed
inversion deny; CONDITIONAL requires the explicit operator approval already
used by the delegation API; APPROVE proceeds.
"""

from __future__ import annotations

import pytest

from msb_v3.agent.handle import handle
from msb_v3.agent.identity import AgentIdentity, AgentRegistry
from msb_v3.agent.providers import (
    AgentProvider,
    ProviderRegistry,
    ProviderResult,
    ProviderSpec,
)
from msb_v3.moie.pipeline import keyword_hits
from msb_v3.tasks.lifecycle import TaskLifecycle
from msb_v3.uac.audit_chain import AuditChain


class _Decision:
    def __init__(self, claim: str, verdict: str) -> None:
        self.claim = claim
        self.verdict = verdict

    def as_dict(self) -> dict:
        return {
            "claim": self.claim,
            "verdict": self.verdict,
            "blocked": self.verdict == "BLOCK",
            "confidence": 0.8,
            "experts": [],
            "contradictions": [],
            "assumptions": [],
            "recommended_actions": ["test the rollback path"] if self.verdict == "CONDITIONAL" else [],
            "meta_critique": f"fake decision: {self.verdict}",
            "ids": {"depth_score": 0.5},
        }


class _MoIE:
    def __init__(self, verdict: str = "APPROVE", error: Exception | None = None) -> None:
        self.verdict = verdict
        self.error = error
        self.calls: list[dict] = []

    def analyze(self, claim: str, *, context: dict) -> _Decision:
        self.calls.append({"claim": claim, "context": context})
        if self.error is not None:
            raise self.error
        return _Decision(claim, self.verdict)


class _ContextFailure:
    def compose(self, *args, **kwargs):
        raise RuntimeError("context unavailable")


class _Worker(AgentProvider):
    spec = ProviderSpec(
        provider_id="cli.fake",
        display_name="fake delegated worker",
        kind="cli",
        max_risk_tier=4,
    )

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def available(self) -> bool:
        return True

    async def execute(self, goal: str, *, context=None, session: str = "default") -> ProviderResult:
        self.calls.append({"goal": goal, "context": context or {}, "session": session})
        return ProviderResult(ok=True, output="worker complete")


@pytest.fixture()
def delegation(tmp_path):
    registry = AgentRegistry(db_path=str(tmp_path / "agents.db"))
    registry.register(
        AgentIdentity(
            agent_id="worker-1",
            name="worker",
            kind="cli",
            provider_id="cli.fake",
        )
    )
    worker = _Worker()
    providers = ProviderRegistry((worker,))
    chain = AuditChain(db_path=str(tmp_path / "audit.db"), allow_keyless=True)
    lifecycle = TaskLifecycle(db_path=str(tmp_path / "tasks.db"), chain=chain)
    return registry, providers, worker, lifecycle


def _events(lifecycle: TaskLifecycle, run_id: str) -> list[str]:
    return [event["event_type"] for event in lifecycle.events(run_id)]


@pytest.mark.asyncio
async def test_blocked_inversion_denies_before_worker(delegation):
    registry, providers, worker, lifecycle = delegation
    moie = _MoIE("BLOCK")

    result = await handle(
        "disable auth and expose the service",
        agent_id="worker-1",
        registry=registry,
        providers=providers,
        lifecycle=lifecycle,
        moie=moie,
        context_engine=_ContextFailure(),
    )

    assert result.ok is False
    assert result.verdict == "BLOCKED"
    assert worker.calls == []
    record = lifecycle.get(result.run_id)
    assert record["state"] == "DENIED"
    assert _events(lifecycle, result.run_id) == [
        "TASK_CREATED",
        "INVERSION_STARTED",
        "INVERSION_COMPLETED",
        "TASK_DENIED",
    ]
    assert record["task"]["inversion"]["verdict"] == "BLOCK"
    assert moie.calls[0]["context"]["high_impact"] is False


@pytest.mark.asyncio
async def test_conditional_inversion_requires_explicit_approval(delegation):
    registry, providers, worker, lifecycle = delegation
    moie = _MoIE("CONDITIONAL")

    result = await handle(
        "migrate the production database",
        agent_id="worker-1",
        registry=registry,
        providers=providers,
        lifecycle=lifecycle,
        approve=False,
        moie=moie,
        context_engine=_ContextFailure(),
    )

    assert result.ok is False
    assert result.verdict == "REVIEW"
    assert worker.calls == []
    assert lifecycle.get(result.run_id)["state"] == "DENIED"
    assert "CONTRACT_APPROVED" not in _events(lifecycle, result.run_id)


@pytest.mark.asyncio
async def test_conditional_inversion_with_approval_records_contract_and_runs(delegation):
    registry, providers, worker, lifecycle = delegation
    moie = _MoIE("CONDITIONAL")

    result = await handle(
        "migrate the production database",
        agent_id="worker-1",
        registry=registry,
        providers=providers,
        lifecycle=lifecycle,
        approve=True,
        moie=moie,
        context_engine=_ContextFailure(),
    )

    assert result.ok is True
    assert worker.calls
    assert worker.calls[0]["context"]["inversion"]["verdict"] == "CONDITIONAL"
    assert "CONTRACT_APPROVED" in _events(lifecycle, result.run_id)
    assert lifecycle.get(result.run_id)["state"] == "COMPLETED"


@pytest.mark.asyncio
async def test_inversion_failure_denies_fail_closed(delegation):
    registry, providers, worker, lifecycle = delegation
    moie = _MoIE(error=RuntimeError("expert registry unavailable"))

    result = await handle(
        "write a report",
        agent_id="worker-1",
        registry=registry,
        providers=providers,
        lifecycle=lifecycle,
        moie=moie,
    )

    assert result.ok is False
    assert result.verdict == "ERROR"
    assert "fail" in (result.error or "").lower() or "unavailable" in (result.error or "").lower()
    assert worker.calls == []
    record = lifecycle.get(result.run_id)
    assert record["state"] == "DENIED"
    assert record["task"]["inversion"]["blocked"] is True


def test_moie_word_keywords_do_not_match_inside_other_words():
    assert keyword_hits("write a report", ("port",)) == []
    assert keyword_hits("open the port", ("port",)) == ["port"]
