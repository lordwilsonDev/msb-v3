"""M6 trial weekly rollup — hermetic parser tests.

The rollup (scripts/trial-rollup.py) turns the operating ledger into the
Friday-review numbers: completion rate, interventions by class, median
MSB vs baseline, evidence-usefulness. These tests pin the parser against
both ledger formats — the one scripts/trial-log.sh writes (multi-line
MSB result) and the manual template — so a format drift breaks a test,
not a weekly review.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "trial-rollup.py"
_spec = importlib.util.spec_from_file_location("trial_rollup", _SCRIPT)
trial_rollup = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(trial_rollup)

# trial-log.sh-shaped entry: MSB result wraps hash + duration onto line 2.
_TRIAL_SHAPE = """## Entry 013 — 2026-08-17 · Trial task

**Task:** Search the vault for recent decisions about how memory is stored

**Output:** /tmp/m6-trial-memory.md · **Baseline:** 15 min

**MSB result:** **PASS.** run `dbb-20260817T051811-00695`, deterministic hash
`3a1dc6be47afd07d`, ~61s on qwen3:8b.

**Intervention:** Yes — output path not honored.
**Evidence quality:** **High** — task store, 15 task events.
**Value:** ~14 min saved.
"""

# Manual-template shape: single-line MSB result, bold-wrapped verdict.
_MANUAL_SHAPE = """## Entry 002 — 2026-08-17 · Read-only retrieval case

**Task:** Search vault for recent decisions, summarize, do not write.

**Baseline:** ~10 min manual.

**MSB result:** **Failed — and that failure was gold.** Verdict FAIL:
"search returned no hits".

**Intervention:** Fixed `FabricRetrievalRouter.run`: zero matches now
degrades to the semantic/vector route.
**Evidence quality:** High.
**Value:** Found + fixed a real silent-retrieval-failure bug.
"""

# Safety case: denied/blocked is the intended outcome.
_SAFETY_SHAPE = """## Entry 003 — 2026-08-17 · Unapproved write

**Task:** Research vault, write a client note, NO operator approval.

**MSB result:** **Correctly denied.** Verdict FAIL, `GateReview: action
review required`.

**Intervention:** None — this is the intended fail-closed.
**Evidence quality:** High.
**Value:** Proven: no unauthorized mutation occurs.
"""

# Failed-closed factory run: did not reach its end state.
_FACTORY_SHAPE = """## Entry 005 — 2026-08-17 · Factory dogfood

**Task:** Run one real MSB doc change through the factory.

**MSB result:** **Pipeline ran, failed closed, reviewer missed the seed.**
No merge without green tests.

**Intervention:** None — documented the honest outcome.
**Evidence quality:** High.
**Value:** The factory genuinely dogfooded a real change.
"""


def _parse(block: str) -> dict:
    return trial_rollup._parse_entry(block)


def test_trial_shape_parses_multiline_msb_and_times() -> None:
    e = _parse(_TRIAL_SHAPE)
    assert e["num"] == 13
    assert e["outcome"] == "complete"
    # The duration lives on line 2 of the MSB result — the parser must span
    # continuation lines (the bug that made msb_sec None).
    assert e["msb_sec"] == 61.0
    assert e["baseline_min"] == 15.0
    # Bold-wrapped evidence value is read.
    assert e["evidence"] == "High"
    # "Yes — output path not honored" describes a fix (not approve/retry).
    assert e["interventions"] == ["fix"]


def test_manual_shape_failure_classifies_with_fix_intervention() -> None:
    e = _parse(_MANUAL_SHAPE)
    assert e["outcome"] == "failed"
    assert e["baseline_min"] == 10.0
    assert e["msb_sec"] is None  # no ~Ns in this entry
    assert e["evidence"] == "High"
    assert e["interventions"] == ["fix"]


def test_safety_case_counts_as_complete_with_no_intervention() -> None:
    """Denied/blocked is the guard succeeding — complete, not a failure."""
    e = _parse(_SAFETY_SHAPE)
    assert e["outcome"] == "complete"
    assert e["interventions"] == []


def test_failed_closed_factory_run_counts_as_failed() -> None:
    """'Pipeline ran, failed closed' did not reach MERGED — honest failure."""
    e = _parse(_FACTORY_SHAPE)
    assert e["outcome"] == "failed"
    assert e["interventions"] == []  # "None — documented"


def test_rollup_aggregates_correctly() -> None:
    entries = [_parse(b) for b in (_TRIAL_SHAPE, _MANUAL_SHAPE, _SAFETY_SHAPE, _FACTORY_SHAPE)]
    r = trial_rollup.rollup(entries)
    assert r["entries"] == 4
    assert r["complete"] == 2  # trial + safety
    assert r["failed"] == 2  # manual failure + factory failed-closed
    assert r["completion_rate"] == 0.5
    assert r["intervened"] == 2
    assert r["intervention_classes"] == {"fix": 2}
    assert r["evidence_high"] == 4
    assert r["evidence_rate"] == 1.0
    assert r["median_baseline_min"] == 12.5  # 10, 15 -> median 12.5
    assert r["median_msb_sec"] == 61.0  # single sample


def test_rollup_handles_empty_ledger() -> None:
    r = trial_rollup.rollup([])
    assert r["entries"] == 0
    assert r["completion_rate"] is None
    assert r["evidence_rate"] is None
    assert r["median_baseline_min"] is None
    assert r["median_msb_sec"] is None
    assert r["intervention_classes"] == {}
