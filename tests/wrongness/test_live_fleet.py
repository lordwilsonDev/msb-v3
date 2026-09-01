"""The first live claim: the specialist-fleet bake-off.

The claim "a specialist fleet with deterministic routing beats a single
generalist" gets its falsification data from ~/specialist-fleet/results.
Three deterministic gates adjudicate it (M5 — the audit found only the
code gate wired; the automation-class refutation was unconnected):

- router gate   (round0_setfit.json: 0.964 >= 0.90)     -> confirming
- code gate     (round1_code.json: 0.84 vs 0.62, 4.6x)  -> confirming
- automation    (round1_automation.json: 0.467 vs 0.60) -> REFUTING

Mixed evidence -> CONFLICTING verdict: forward mode returns a sharp,
non-neutral answer instead of the zero-shot moderator's neutral default
(the DebateCV failure mode the audit flagged).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from msb_v3.wrongness.claims import Claim
from msb_v3.wrongness.engine import WrongnessEngine

FLEET_ROOT = Path.home() / "specialist-fleet"
FLEET_RESULTS = FLEET_ROOT / "results"
CLAIM_FILE = Path(__file__).resolve().parents[2] / "src" / "msb_v3" / "wrongness" / "claims" / "fleet_bakeoff.json"


def _load_claim() -> Claim:
    return Claim.from_dict(json.loads(CLAIM_FILE.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def fleet_claim() -> Claim:
    return _load_claim()


def _by_round(result) -> dict[str, object]:
    return {c.check.split("(")[1].rstrip(")"): c for c in result.checks}


@pytest.mark.skipif(not FLEET_RESULTS.exists(), reason="specialist-fleet results absent")
def test_fleet_claim_has_three_adjudicated_gates(fleet_claim: Claim) -> None:
    engine = WrongnessEngine(FLEET_ROOT)
    result = engine.run(fleet_claim)
    assert len(result.checks) == 3
    by_round = _by_round(result)
    assert "round0_setfit.json" in by_round
    assert "round1_code.json" in by_round
    assert "round1_automation.json" in by_round


@pytest.mark.skipif(not FLEET_RESULTS.exists(), reason="specialist-fleet results absent")
def test_fleet_router_and_code_gates_pass(fleet_claim: Claim) -> None:
    """The confirming evidence: router >= 0.90 and code within 5% + >=2x faster."""
    engine = WrongnessEngine(FLEET_ROOT)
    result = engine.run(fleet_claim)
    by_round = _by_round(result)
    assert by_round["round0_setfit.json"].ok is True, by_round["round0_setfit.json"].evidence
    assert by_round["round1_code.json"].ok is True, by_round["round1_code.json"].evidence


@pytest.mark.skipif(not FLEET_RESULTS.exists(), reason="specialist-fleet results absent")
def test_fleet_automation_gate_refutes(fleet_claim: Claim) -> None:
    """The falsification boundary (M5): 0.467 vs 0.60 is below the 5% band."""
    engine = WrongnessEngine(FLEET_ROOT)
    result = engine.run(fleet_claim)
    by_round = _by_round(result)
    assert by_round["round1_automation.json"].ok is False, by_round["round1_automation.json"].evidence


@pytest.mark.skipif(not FLEET_RESULTS.exists(), reason="specialist-fleet results absent")
def test_fleet_verdict_is_conflicting_not_neutral(fleet_claim: Claim) -> None:
    """Forward mode must NOT default to the neutral CHECK: confirming and
    refuting evidence coexist -> CONFLICTING, routed to a human."""
    engine = WrongnessEngine(FLEET_ROOT)
    result = engine.run(fleet_claim)
    assert result.verdict == "CONFLICTING", result.verdict
    assert result.urgency > 0.0
