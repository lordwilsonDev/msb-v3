"""M5 — soak run (blueprint §M5 exit criterion: "a repeatable run report
covers a meaningful workload sample").

The failure matrix proves each mode individually; the soak proves the LOOP
under a realistic, seeded workload mix through the real executor +
SafeProvider + ActionGate + a real audit chain, and asserts the scoreboard
targets:

    completion_rate       >= 0.90 on supported cases
    unsafe_escape_rate    == 0  (no BLOCK/REVIEW action executed its tool)
    evidence_completeness >= 0.98 (every gate refusal produced an audit record)
    recovery_rate         >= 0.80 (retried-then-succeeded / all retried)

Hermetic: fake tool providers, no model, no network — deterministic per
seed, so the same seed always produces the same mix and the same outcomes.
"""
from __future__ import annotations

import pytest

from msb_v3.observability.soak import SCENARIOS, run_soak


@pytest.mark.asyncio
async def test_soak_meets_scoreboard_targets(tmp_path) -> None:
    report = await run_soak(n_runs=60, seed=7, db_path=str(tmp_path / "soak.db"))
    metrics = report.to_dict()["metrics"]

    # Every scenario type appears (the mix is the point).
    counts = report.to_dict()["scenarios"]
    for name in SCENARIOS:
        assert counts[name] > 0, f"scenario {name} never ran"

    # 1. No silent unsafe continuation: a refused action must never execute.
    assert report.unsafe_escape_rate() == 0.0
    assert metrics["unsafe_escape_rate"] == 0.0
    for r in report.runs:
        assert r.safe, f"run {r.index} ({r.scenario}) executed a refused action"

    # 2. Evidence completeness: every gate refusal produced an audit record.
    assert metrics["evidence_completeness"] >= 0.98
    assert report.evidence_completeness() >= 0.98

    # 3. Recovery is bounded AND effective: retried tasks mostly recover.
    assert metrics["recovery_rate"] >= 0.80
    assert metrics["total_retries"] > 0  # the workload actually retried

    # 4. Completion on supported cases (happy + recover) is high.
    assert metrics["completion_rate"] >= 0.90

    # 5. Outcomes match their known expected terminal states (no fabricated
    #    success — a scenario expected to fail did fail, visibly).
    for r in report.runs:
        assert r.ok == r.expected_ok, (
            f"run {r.index} ({r.scenario}): expected ok={r.expected_ok}, got {r.ok} "
            f"error={r.error!r}"
        )
        if not r.ok:
            assert r.error, f"run {r.index} ({r.scenario}) failed without a visible error"


@pytest.mark.asyncio
async def test_soak_is_deterministic_per_seed(tmp_path) -> None:
    """Same seed -> same scenario mix -> same outcomes (repeatable report).
    Latency metrics are excluded — wall-clock timing is inherently
    non-deterministic; the scenario mix and every logical outcome must match
    exactly."""
    a = await run_soak(n_runs=40, seed=11, db_path=str(tmp_path / "a.db"))
    b = await run_soak(n_runs=40, seed=11, db_path=str(tmp_path / "b.db"))
    assert [r.scenario for r in a.runs] == [r.scenario for r in b.runs]
    assert [r.ok for r in a.runs] == [r.ok for r in b.runs]
    assert [r.retries for r in a.runs] == [r.retries for r in b.runs]
    assert [r.gate_refusals for r in a.runs] == [r.gate_refusals for r in b.runs]
    a_metrics = a.to_dict()["metrics"]
    b_metrics = b.to_dict()["metrics"]
    for key in a_metrics:
        if key.startswith("p"):  # p50/p95 latency — timing, not logic
            continue
        assert a_metrics[key] == b_metrics[key], key


@pytest.mark.asyncio
async def test_soak_denials_and_taint_are_refused_with_evidence(tmp_path) -> None:
    """The adversarial scenarios specifically: BLOCK and REVIEW must refuse
    (no tool call) and leave an audit record."""
    report = await run_soak(n_runs=200, seed=3, db_path=str(tmp_path / "soak.db"))
    denied = [r for r in report.runs if r.scenario == "denied"]
    tainted = [r for r in report.runs if r.scenario == "tainted"]
    assert denied and tainted
    for r in denied + tainted:
        assert r.ok is False
        assert r.safe is True
        assert r.gate_refusals == 1
        assert r.gate_audit_records == 1, f"{r.scenario} run {r.index} left no audit record"


@pytest.mark.asyncio
async def test_soak_report_shape(tmp_path) -> None:
    report = await run_soak(n_runs=10, seed=1, db_path=str(tmp_path / "soak.db"))
    data = report.to_dict()
    assert data["runs"] == 10
    assert set(data["metrics"]) >= {
        "completion_rate", "unsafe_escape_rate", "evidence_completeness",
        "recovery_rate", "escalation_rate", "total_retries",
        "p50_latency_s", "p95_latency_s",
    }
    assert data["targets"]["unsafe_escape_rate_max"] == 0.0
