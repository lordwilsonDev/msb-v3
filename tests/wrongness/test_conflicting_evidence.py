"""Inversion-audit closures (03_Inversion-Audit.md M1-M4).

- M1: the engine runs on itself — the self-claim's deterministic check IS
      the engine replaying its own corpus.
- M2: CONFLICTING verdict state — evidence pointing both ways (supporting
      evidence + a refuting signal) is representable and distinct.
- M3: blind replay (recorded routing disabled) + held-out half stability.
- M4: urgency = severity x consequence; pass consensus never escalates.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from msb_v3.wrongness.checks import run_check
from msb_v3.wrongness.claims import CheckSpec, Claim, Finding
from msb_v3.wrongness.engine import (
    WrongnessEngine,
    load_corpus,
    run_replay,
    split_held_out,
)
from msb_v3.wrongness.passes import run_all_passes
from msb_v3.wrongness.policy import (
    CHECK,
    CONFLICTING,
    ESCALATE,
    NOTE,
    claim_verdict,
    passes_agreeing,
    urgency_score,
)

PKG = Path(__file__).resolve().parents[2] / "src" / "msb_v3" / "wrongness"
CORPUS = PKG / "corpus" / "byhand_21.json"
SELF_CLAIM = PKG / "claims" / "self_claim.json"
REPO = Path(__file__).resolve().parents[2]


# --- M2: CONFLICTING verdict state -------------------------------------------


def test_conflicting_requires_both_signals() -> None:
    """A refuting signal alone is ESCALATE; only refuting+confirming is CONFLICTING."""
    refuting = [Finding(pass_name="boundary", tier=ESCALATE, statement="check failed")]
    assert claim_verdict(refuting) == ESCALATE

    both = refuting + [Finding(pass_name="evidence", tier=NOTE, statement="support stands")]
    assert claim_verdict(both) == CONFLICTING

    confirming_only = [Finding(pass_name="boundary", tier=NOTE, statement="check passed")]
    assert claim_verdict(confirming_only) == NOTE


def test_conflicting_reachable_through_engine() -> None:
    """A claim with supporting evidence and a failing check runs to CONFLICTING."""
    # A call-site probe for a symbol that provably does not exist: n=0 < min=1
    # -> deterministic refutation, hermetic to whatever tree the suite runs in.
    # The symbol is assembled from parts so the contiguous word never appears in
    # ANY tracked file (including this one, once it is committed) — the literal
    # would match its own source line and flip the probe.
    symbol = "".join(["zz_wrongness", "_conflict_probe"])
    claim = Claim(
        id="t-conflict",
        statement="the report is accurate",
        domain="process",
        supporting_evidence=("report says OK",),
        checks=(CheckSpec(kind="call_sites", params={"symbol": symbol, "min_count": 1}),),
    )
    result = WrongnessEngine(REPO).run(claim)
    assert result.verdict == CONFLICTING
    assert any(f.pass_name == "evidence" and f.tier == NOTE for f in result.findings)
    assert any(f.tier == ESCALATE for f in result.findings)


def test_corpus_b1_b2_conflict_pair_is_encoded() -> None:
    """The by-hand corpus's B1/B2 pair is the CONFLICTING evidence case (M2)."""
    corpus = load_corpus(CORPUS)
    by_id = {c.id: c for c in corpus}
    assert "B2" in by_id["B1"].conflicts_with
    assert "B1" in by_id["B2"].conflicts_with
    assert by_id["B1"].supporting_evidence  # the closer report stands as recorded support
    # And the replay represents B1 as conflicting evidence while still flagging it:
    findings = run_all_passes(by_id["B1"])
    assert claim_verdict(findings) == CONFLICTING


def test_replay_still_counts_conflicting_as_flag() -> None:
    """CONFLICTING counts as a predicted failure (B1 is a HIT) — PEDR stays 1.0."""
    corpus = load_corpus(CORPUS)
    score = run_replay(corpus)
    assert score.pedr == pytest.approx(1.0)
    assert score.actual_failures == 15
    assert score.decision == "VALIDATED"


# --- M4: urgency = severity x consequence, rubric-consensus --------------------


def test_urgency_multiplies_severity_by_consequence() -> None:
    esc = [Finding(pass_name="boundary", tier=ESCALATE, statement="failed")]
    chk = [Finding(pass_name="attack", tier=CHECK, statement="investigate")]
    assert urgency_score(esc, "low") == pytest.approx(0.5)
    assert urgency_score(esc, "high") == pytest.approx(1.0)
    # High-consequence CHECK outranks low-consequence ESCALATE on the scale:
    assert urgency_score(chk, "high") > urgency_score(esc, "low")
    assert urgency_score([], "high") == 0.0


def test_pass_consensus_never_escalates() -> None:
    """Six passes agreeing at CHECK is still CHECK — consensus is routing, not evidence."""
    findings = [Finding(pass_name=p, tier=CHECK, statement="x") for p in ("attack", "counterexample", "assumption", "boundary", "incentive", "scaling", "failure_cascade")]
    assert len(passes_agreeing(findings, CHECK)) == 7
    assert claim_verdict(findings) == CHECK


# --- M1: the engine runs on itself --------------------------------------------


def test_self_claim_reproduces_byhand_verdict() -> None:
    claim = Claim.from_dict(json.loads(SELF_CLAIM.read_text(encoding="utf-8")))
    result = WrongnessEngine(REPO).run(claim)
    assert len(result.checks) == 1
    assert result.checks[0].ok is True, result.checks[0].evidence
    assert "PEDR" in result.checks[0].evidence
    # The engine's own claim survives scrutiny: not an escalation.
    assert result.verdict in (CHECK, NOTE), result.verdict
    assert result.urgency > 0.0  # high consequence feeds urgency


def test_corpus_replay_check_fails_when_gate_moved() -> None:
    """The self-claim is falsifiable: raise the bar and the check fails."""
    spec = CheckSpec(kind="corpus_replay", params={"min_pedr": 1.01, "require_decision": "VALIDATED"})
    res = run_check(spec, REPO)
    assert res.ok is False
    assert "PEDR" in res.evidence


def test_corpus_replay_check_inconclusive_on_bad_corpus(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    spec = CheckSpec(kind="corpus_replay", params={"corpus_path": str(bad)})
    assert run_check(spec, REPO).ok is None


# --- M3: blind replay + held-out halves ---------------------------------------


def test_blind_mode_quantifies_trivial_flagging() -> None:
    """Without recorded routing every claim lands at CHECK — PEDR 1.0 is free
    (CHECK flags everything) but NOTHING escalates.  That is the crying-wolf
    cost quantified: discrimination lives in the checks, not the passes."""
    corpus = load_corpus(CORPUS)
    blind = run_replay(corpus, use_recorded_routing=False)
    assert blind.blind is True
    assert blind.pedr == pytest.approx(1.0)
    assert blind.false_positives_assertion == 0  # nothing escalates blind
    assert blind.false_positives_strict == 6  # every FP still flagged at CHECK


def test_recorded_vs_blind_difference_is_the_author_leak() -> None:
    """The gap between recorded routing and blind mode is the by-hand
    annotations doing the work — the honest measure of M3's leakage."""
    corpus = load_corpus(CORPUS)
    recorded = run_replay(corpus, use_recorded_routing=True)
    blind = run_replay(corpus, use_recorded_routing=False)
    # Recorded routing escalates the failure-assertions (3 of which were FPs);
    # blind mode escalates none.
    assert recorded.false_positives_assertion == 3
    assert blind.false_positives_assertion == 0


def test_held_out_halves_are_stable() -> None:
    """The §VII decision must survive on each deterministic half (M3)."""
    corpus = load_corpus(CORPUS)
    halves = split_held_out(corpus)
    assert len(halves) == 2
    assert len(halves[0]) + len(halves[1]) == len(corpus)
    for half in halves:
        score = run_replay(half)
        assert score.decision == "VALIDATED", score
        assert score.pedr == pytest.approx(1.0)
