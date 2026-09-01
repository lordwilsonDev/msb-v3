"""The MVP must reproduce the by-hand §VII experiment's verdict.

This is the "beat the cheaper alternative" gate: the by-hand run is the
cheaper alternative, and the machine must at least reproduce its numbers —
PEDR 1.0, FP 16.7% (assertion) / 28.6% (strict), decision VALIDATED.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from msb_v3.wrongness.engine import load_corpus, run_replay

CORPUS = Path(__file__).resolve().parents[2] / "src" / "msb_v3" / "wrongness" / "corpus" / "byhand_21.json"


@pytest.fixture(scope="module")
def corpus() -> list:
    return load_corpus(CORPUS)


def test_corpus_has_23_rows_21_decisions(corpus: list) -> None:
    """Doc says 21 decisions; its tables list 23 rows. Reconciliation: A8 is
    one decision carrying two latent hazards (hit_weight 2), and B2 is folded
    into B1 as confirming evidence, not a separate claim."""
    assert len(corpus) == 23


def test_pedr_is_one(corpus: list) -> None:
    score = run_replay(corpus)
    assert score.actual_failures == 15  # A8 carries hit_weight 2
    assert score.predicted_failures == 15
    assert score.pedr == pytest.approx(1.0)


def test_fp_rates_match_byhand(corpus: list) -> None:
    score = run_replay(corpus)
    # Assertion semantics: only failure-assertions (C1, C2, C3) count.
    assert score.false_positives_assertion == 3
    assert score.fp_rate_assertion == pytest.approx(3 / 18, abs=1e-9)  # 16.7%
    # Strict semantics: every flagged finding counts (adds A4, A5, A6).
    assert score.false_positives_strict == 6
    assert score.fp_rate_strict == pytest.approx(6 / 21, abs=1e-9)  # 28.6%


def test_decision_is_validated(corpus: list) -> None:
    score = run_replay(corpus)
    assert score.decision == "VALIDATED"


def test_investigation_prompts_never_escalate(corpus: list) -> None:
    """The load-bearing policy constraint: A4-A6 route to CHECK, not ESCALATE."""
    from msb_v3.wrongness.passes import run_all_passes
    from msb_v3.wrongness.policy import claim_verdict

    for c in corpus:
        if c.id in {"A4", "A5", "A6"}:
            findings = run_all_passes(c)
            assert claim_verdict(findings) == "CHECK", f"{c.id} must not escalate"
            assert all(f.tier != "ESCALATE" for f in findings), f"{c.id} finding escalated"


def test_failure_assertions_do_escalate(corpus: list) -> None:
    """C1-C3 were real escalations that turned out wrong — they must ESCALATE."""
    from msb_v3.wrongness.passes import run_all_passes
    from msb_v3.wrongness.policy import claim_verdict

    for c in corpus:
        if c.id in {"C1", "C2", "C3"}:
            findings = run_all_passes(c)
            assert claim_verdict(findings) == "ESCALATE", f"{c.id} must escalate"
