"""The conversation E2E harness self-tests itself (spec §7): a broken probe
must fail, not silently pass. This wrapper runs the harness's in-process
self-test in the standing suite, so every CI run exercises the probe's own
logic — not just the probe's CI step.

Zero-spend, no server: the self-test verifies stub determinism, fixture
wiring (block vs blocked-answer discrimination), the never-invoke-compose
zero-spend contract, and that the invariant checker is not vacuous.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import probe_conversation_e2e  # noqa: E402


def test_probe_harness_self_test_passes():
    assert probe_conversation_e2e.self_test() == 0


def test_invariant_checker_rejects_ghost_and_blocked_mix():
    """The checker must reject the exact violation shapes the probe guards
    against — proving the assertion helper is not vacuous."""

    # answered without status/evidence_ref (invariant 6) must raise
    with pytest.raises(AssertionError):
        probe_conversation_e2e.assert_envelope_invariants(
            {"schema_version": "1.0", "trace_id": "tr_x", "query": "q",
             "status": "answered", "input_guardrail": {"verdict": "ALLOW"}}
        )
    # a BLOCK response must not pass the answered invariant set
    with pytest.raises(AssertionError):
        probe_conversation_e2e.assert_envelope_invariants(
            {"schema_version": "1.0", "trace_id": "tr_x", "query": "q",
             "status": "blocked", "input_guardrail": {"verdict": "BLOCK"},
             "evidence_ref": "ledger://evidence/conversation_block/x.json"},
            expect_answered=True,
        )
    # a ghost citation (source_id not in sources) must fail the supporting path
    with pytest.raises(AssertionError):
        probe_conversation_e2e._assert_supporting_path({
            "sources": [{"source_id": "a", "score": 0.9, "provenance": {}, "freshness": "FRESH"}],
            "output_guardrail": {"verdict": "SUPPORTING", "citation_rate": 1.0},
            "answer": {"claim_id": "claim:ans:x", "citations": [{"source_id": "ghost"}]},
        })
