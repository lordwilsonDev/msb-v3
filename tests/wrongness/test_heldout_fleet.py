"""Held-out corpus (M9 progress): fleet Round-0/1 decisions, independent outcomes.

The by-hand 21 corpus (``byhand_21.json``) is the engine author's own
retrospective — the circularity the inversion audit flagged (M3/M9).  This
corpus is the first held-out material: outcomes were recorded by the fleet
harness's OWN deterministic gates (SPEC §10 scorecard: within 5% of baseline
AND >=2x faster; 5-fold CV; fresh-set validation) in
``~/specialist-fleet/results/``, before the Wrongness Engine existed.

Independence is test-enforced: no row carries ``escalation_class`` or
``strongest_pass`` (the by-hand routing annotations that leak the author's
judgment), so recorded-routing and blind replay are identical here.  The
checks attached to each row are the same SPEC-defined gates that produced
the recorded outcome — deterministic adjudicators over the real results
JSONs, not author-tuned thresholds.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from msb_v3.wrongness.engine import WrongnessEngine, load_corpus, run_replay

FLEET_ROOT = Path.home() / "specialist-fleet"
FLEET_RESULTS = FLEET_ROOT / "results"
CORPUS_FILE = (
    Path(__file__).resolve().parents[2]
    / "src" / "msb_v3" / "wrongness" / "corpus" / "heldout_fleet_r1.json"
)
BYHAND_FILE = (
    Path(__file__).resolve().parents[2]
    / "src" / "msb_v3" / "wrongness" / "corpus" / "byhand_21.json"
)

NEEDS_FLEET = pytest.mark.skipif(not FLEET_RESULTS.exists(), reason="specialist-fleet results absent")


@pytest.fixture(scope="module")
def heldout() -> list:
    return load_corpus(CORPUS_FILE)


def test_corpus_is_independent(heldout: list) -> None:
    """No by-hand routing annotations — the author-leak source is absent."""
    assert heldout, "held-out corpus is empty"
    for claim in heldout:
        assert claim.escalation_class is None, f"{claim.id} leaks escalation_class"
        assert claim.strongest_pass is None, f"{claim.id} leaks strongest_pass"


def test_corpus_disjoint_from_byhand(heldout: list) -> None:
    byhand_ids = {c.id for c in load_corpus(BYHAND_FILE)}
    assert not ({c.id for c in heldout} & byhand_ids), "held-out rows overlap the by-hand corpus"


def test_corpus_outcome_counts(heldout: list) -> None:
    hits = [c for c in heldout if c.outcome == "HIT"]
    fps = [c for c in heldout if c.outcome == "FP"]
    assert len(hits) == 6, [c.id for c in hits]
    assert len(fps) == 5, [c.id for c in fps]


@NEEDS_FLEET
def test_every_hit_is_flagged(heldout: list) -> None:
    """All 6 real failures are flagged (PEDR 1.0) — the engine does not miss."""
    engine = WrongnessEngine(FLEET_ROOT)
    for claim in heldout:
        if claim.outcome != "HIT":
            continue
        result = engine.run(claim)
        assert result.verdict in ("CHECK", "ESCALATE", "CONFLICTING"), f"{claim.id}: {result.verdict}"


@NEEDS_FLEET
def test_no_confirmed_claim_escalates(heldout: list) -> None:
    """The discrimination test: never ESCALATE a claim the fleet confirmed.

    This is the load-bearing behavior — FP_assertion must be 0.  The
    confirmed rows may land at CHECK (investigate before promote — the
    engine's honest posture on non-machine-verifiable evidence), but never
    at ESCALATE (block) without a refuting signal.
    """
    engine = WrongnessEngine(FLEET_ROOT)
    for claim in heldout:
        if claim.outcome != "FP":
            continue
        result = engine.run(claim)
        assert result.verdict != "ESCALATE", f"{claim.id} escalated a confirmed claim: {result.verdict}"


@NEEDS_FLEET
def test_refuted_claims_escalate_on_evidence(heldout: list) -> None:
    """The checkable refutations must reach ESCALATE (evidence-backed), not CHECK."""
    engine = WrongnessEngine(FLEET_ROOT)
    expected = {"F-R0-1", "F-R0-2", "F-R0-4", "F-R1-3", "F-R1-4", "F-R1-5"}
    by_id = {c.id: c for c in heldout}
    for claim_id in expected:
        result = engine.run(by_id[claim_id])
        assert result.verdict == "ESCALATE", f"{claim_id}: {result.verdict}"


@NEEDS_FLEET
def test_confirmed_claims_stay_at_check(heldout: list) -> None:
    """The confirmed rows with passing gates stay at CHECK — investigate, don't block."""
    engine = WrongnessEngine(FLEET_ROOT)
    by_id = {c.id: c for c in heldout}
    for claim_id in ("F-R0-3", "F-R1-1", "F-R1-2", "F-R1-6"):
        result = engine.run(by_id[claim_id])
        assert result.verdict == "CHECK", f"{claim_id}: {result.verdict}"


@NEEDS_FLEET
def test_heldout_replay_scores(heldout: list) -> None:
    """Replay the held-out corpus with the SPEC §VII rule.

    The meaningful numbers: FP_assertion 0.0 (never blocks a confirmed
    claim) with PEDR 1.0 (catches every real failure).  PEDR 1.0 alone is
    trivial — everything lands at CHECK — which is exactly the M3 lesson;
    the discrimination is in the tier, and this corpus now measures it on
    decisions the engine author did not score.
    """
    score = run_replay(heldout, repo_root=FLEET_ROOT)
    assert score.actual_failures == 6
    assert score.predicted_failures == 6
    assert score.pedr == 1.0
    assert score.fp_rate_assertion == 0.0, score.to_dict()
    assert score.decision == "VALIDATED", score.to_dict()
    # Recorded-routing == blind here (no pins to leak): the two modes must agree.
    blind = run_replay(heldout, repo_root=FLEET_ROOT, use_recorded_routing=False)
    assert blind.pedr == score.pedr
    assert blind.fp_rate_assertion == score.fp_rate_assertion
