'''Evidence receipt — one document reconstructing a governed run.

For every denied OR executed request the receipt must answer: what was
requested -> what was allowed -> what actually happened -> why it was
allowed -> whether it succeeded. This suite proves the reconstruction holds
for a quick-reject BLOCK (denied) and a full PASS (executed), and that a
spine outage degrades gracefully without breaking the receipt.
'''

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _helpers import (  # noqa: E402
    INTENT_WITH_WRITE,
    Audit,
    FakeMoIE,
    FakeProvider,
    SequenceClient,
)

from msb_v3.agent.handle import handle  # noqa: E402
from msb_v3.agent.safety import ActionGate  # noqa: E402
from msb_v3.evidence.receipt import build_evidence_receipt  # noqa: E402
from msb_v3.evidence.spine import DecisionEvidenceStore  # noqa: E402


@pytest.mark.asyncio
async def test_receipt_for_denied_request_is_reconstructable(tmp_path: Path) -> None:
    '''A quick-reject BLOCK leaves a DENY decision vertebra; the receipt
    reconstructs the full chain: requested nothing (denied at gate), allowed
    DENY, happened BLOCKED, why = MoIE BLOCK, succeeded False, 0 model calls,
    and the audit hash is the decision vertebra's content hash.'''
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
    receipt = build_evidence_receipt(
        run_id=result.run_id,
        verdict=result.verdict,
        error=result.error,
        deterministic_hash=result.deterministic_hash,
        trace=result.trace,
        model_calls=result.model_calls,
        spine=spine,
    )

    assert receipt["request_id"] == result.run_id
    assert receipt["moie_verdict"] == "BLOCK"
    assert receipt["authorization_decision"] == "DENY"
    assert receipt["policy_version"] == "handle-gate-v1"
    assert receipt["capability_requested"] == []
    assert receipt["execution_result"]["verdict"] == "BLOCKED"
    assert receipt["execution_result"]["ok"] is False
    assert receipt["model_calls"] == 0
    # the audit hash is the DENY vertebra's chain-linked content hash
    decision = spine.trail(result.run_id)[0]
    assert decision.evidence.kind == "decision"
    assert decision.evidence.policy_result == "DENY"
    assert receipt["audit_hash"] == decision.content_hash
    assert receipt["timestamps"]["decision"] == decision.evidence.timestamp
    assert "why=MoIE quick-reject BLOCK" in receipt["reconstruction"]
    assert "succeeded=False" in receipt["reconstruction"]
    # Evidence language: denied runs are decision-only — nothing was rerun,
    # and the report says so rather than implying a re-execution.
    assert receipt["verification"]["basis"] == "decision-only"
    assert receipt["verification"]["hash_recomputed"] is None
    assert receipt["verification"]["grounded_checks"] == []
    assert receipt["verification"]["log_inference"]["basis"] == "inferred-from-logs"
    assert receipt["verification"]["log_inference"]["where"] == f"/agent/tasks/{result.run_id}/replay"
    assert "verified=decision-only" in receipt["reconstruction"]


@pytest.mark.asyncio
async def test_receipt_for_executed_request_is_reconstructable(tmp_path: Path) -> None:
    '''A PASS leaves decision -> execution -> verification vertebrae; the
    receipt reconstructs what was allowed (ALLOW), why (MoIE verdict +
    authorization decision), what happened (PASS), the verification hash,
    and the real model-call count (>= 2: intent + plan).'''
    spine = DecisionEvidenceStore(str(tmp_path / "spine.db"))
    result = await handle(
        "research the vault and write a client brief",
        client=SequenceClient(INTENT_WITH_WRITE),
        approve=True,
        provider=FakeProvider(tmp_path),
        gate=ActionGate(audit_chain=Audit()),
        moie=FakeMoIE("APPROVE"),
        spine=spine,
    )

    assert result.verdict == "PASS"
    receipt = build_evidence_receipt(
        run_id=result.run_id,
        verdict=result.verdict,
        error=result.error,
        deterministic_hash=result.deterministic_hash,
        trace=result.trace,
        model_calls=result.model_calls,
        spine=spine,
    )

    assert receipt["moie_verdict"] == "APPROVE"
    assert receipt["authorization_decision"] == "ALLOW"
    assert receipt["policy_version"] == "handle-gate-v1"
    assert set(receipt["capability_requested"]) == {"read_vault", "write_file"}
    assert receipt["execution_result"]["verdict"] == "PASS"
    assert receipt["execution_result"]["ok"] is True
    assert receipt["verification_result"] == result.deterministic_hash
    assert receipt["model_calls"] >= 2  # intent + plan (the fake chat tool doesn't call the model)
    trail = spine.trail(result.run_id)
    kinds = [r.evidence.kind for r in trail]
    assert kinds == ["decision", "execution", "verification"]
    assert receipt["audit_hash"] == trail[0].content_hash
    assert receipt["timestamps"]["execution"] == trail[1].evidence.timestamp
    assert receipt["timestamps"]["verification"] == trail[2].evidence.timestamp
    assert "happened=PASS" in receipt["reconstruction"]
    assert "succeeded=True" in receipt["reconstruction"]
    # Evidence language: executed runs are rerun — the grounded checks were
    # executed against ground truth and the hash recomputes from the trace.
    assert receipt["verification"]["basis"] == "rerun"
    assert receipt["verification"]["hash_recomputed"] is True
    checks = receipt["verification"]["grounded_checks"]
    assert {c["check"] for c in checks} == {
        "search_returned_hits",
        "synthesis_nonempty",
        "file_written_with_heading",
    }
    assert all(c["trust"] == "high" for c in checks)
    assert all(c["verdict"] == "pass" for c in checks)
    assert "verified=rerun" in receipt["reconstruction"]


@pytest.mark.asyncio
async def test_receipt_without_spine_falls_back_honestly(tmp_path: Path) -> None:
    '''A spine outage must not break the receipt: the HandleResult alone
    still reconstructs the run, with the spine fields at their honest
    fallbacks.'''
    result = await handle(
        "rm -rf production",
        client=SequenceClient(INTENT_WITH_WRITE),
        approve=True,
        provider=FakeProvider(tmp_path),
        gate=ActionGate(audit_chain=Audit()),
        moie=FakeMoIE("BLOCK"),
        spine=None,
    )
    receipt = build_evidence_receipt(
        run_id=result.run_id,
        verdict=result.verdict,
        error=result.error,
        deterministic_hash=result.deterministic_hash,
        trace=result.trace,
        model_calls=result.model_calls,
        spine=None,
    )
    assert receipt["authorization_decision"] == "DENY"
    assert receipt["moie_verdict"] == "BLOCK"
    assert receipt["model_calls"] == 0
    assert receipt["audit_hash"] is None  # no spine record to link
    assert receipt["timestamps"]["decision"] is None
    assert receipt["verification"]["basis"] == "decision-only"  # denied: nothing to rerun
