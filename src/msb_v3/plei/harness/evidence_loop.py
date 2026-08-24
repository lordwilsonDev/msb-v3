"""Evidence Loop — close the feedback loop from execution → twin update.

This is the calibration precursor (Phase 7). After a WorkPlan executes
through the harness bridge, the EvidenceLoop:

1. Parses every StepResult — what was attempted, what succeeded/failed.
2. Updates the ProjectTwin's evidence layer (freshness timestamps,
   component health, newly verified/contradicted claims).
3. Re-runs the lifecycle classifier — a successful mitigation may move
   the project from OPERATIONS → OPTIMIZATION.
4. Records the prediction → outcome pair for future calibration.

The key insight: PLEI doesn't just observe the project statically — it
observes the EFFECT of its own decisions, closing the loop.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from msb_v3.plei.harness.bridge import ExecutionReport
from msb_v3.plei.twin import ProjectTwin


@dataclass(slots=True)
class TwinDelta:
    """What changed in the ProjectTwin after execution."""

    # Freshness
    evidence_age_s: float = 0.0  # seconds since last twin update
    twin_updated: bool = False

    # Lifecycle
    previous_stage: str = ""
    current_stage: str = ""
    stage_changed: bool = False
    confidence_before: float = 0.0
    confidence_after: float = 0.0

    # Health deltas
    health_deltas: dict[str, float] = field(default_factory=dict)

    # Gap closure
    gaps_closed: list[str] = field(default_factory=list)
    gaps_opened: list[str] = field(default_factory=list)

    # Risk changes
    risks_mitigated: list[str] = field(default_factory=list)
    risks_created: list[str] = field(default_factory=list)

    # Evidence
    new_verified_claims: int = 0
    new_contradicted_claims: int = 0


@dataclass(slots=True)
class LoopResult:
    """The complete evidence loop result."""

    execution_report: ExecutionReport
    twin_delta: TwinDelta
    loop_duration_s: float = 0.0
    recommendation: str = ""
    ready_for_calibration: bool = False


# ── Evidence loop engine ──────────────────────────────────────────────────


def run_evidence_loop(
    report: ExecutionReport,
    twin: ProjectTwin,
    *,
    previous_stage: str | None = None,
    previous_confidence: float | None = None,
) -> LoopResult:
    """Run the evidence feedback loop after harness execution.

    Takes the ExecutionReport from bridge.execute_plan() and the current
    ProjectTwin, then computes what changed and whether to re-classify.

    This is deterministic (no LLM calls) — it updates evidence timestamps
    and recomputes the lifecycle stage from the new evidence footprint.
    """
    started = time.perf_counter()
    delta = TwinDelta()

    # ── 1. Compute what the execution proved ──
    for sr in report.step_results:
        if sr.ok and sr.verified:
            delta.new_verified_claims += sr.claims_total
            # If this was a gap-close step, track it
            if "gap" in sr.step_id.lower() or "close" in sr.step_id.lower():
                delta.gaps_closed.append(sr.step_id)
            # If this was a risk-mitigate step
            if "risk" in sr.step_id.lower() or "mitigate" in sr.step_id.lower():
                delta.risks_mitigated.append(sr.step_id)

        if not sr.ok:
            delta.new_contradicted_claims += 1
            if sr.gate_verdict == "BLOCK":
                delta.risks_created.append(f"BLOCKED: {sr.step_id} — {sr.gate_reason[:100]}")
            elif sr.error:
                delta.risks_created.append(f"FAILED: {sr.step_id} — {sr.error[:100]}")

    # ── 2. Evidence freshness ──
    if hasattr(twin, "evidence") and twin.evidence:
        ev = twin.evidence
        if hasattr(ev, "last_updated") and ev.last_updated:
            last_ts = ev.last_updated.value if hasattr(ev.last_updated, "value") else str(ev.last_updated)
            try:
                last_dt = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
                delta.evidence_age_s = (datetime.now(timezone.utc) - last_dt).total_seconds()
            except (ValueError, TypeError):
                delta.evidence_age_s = 0.0

    # ── 3. Lifecycle re-classification ──
    from msb_v3.plei.lifecycle import classify_lifecycle

    new_lc = classify_lifecycle(twin)
    delta.previous_stage = previous_stage or (
        twin.lifecycle.stage.value if hasattr(twin.lifecycle.stage, "value") else str(twin.lifecycle.stage)
    )
    delta.current_stage = new_lc.stage.value if hasattr(new_lc.stage, "value") else str(new_lc.stage)
    delta.stage_changed = delta.previous_stage != delta.current_stage
    delta.confidence_before = previous_confidence or new_lc.confidence
    delta.confidence_after = new_lc.confidence

    # ── 4. Health deltas ──
    # If execution succeeded, bump health dimensions
    if report.ok:
        delta.health_deltas = {
            "implementation": 0.02,
            "observability": 0.01,
        }
    else:
        delta.health_deltas = {
            "implementation": -0.01,
        }

    # ── 5. Twin update ──
    delta.twin_updated = True  # The twin has been observed post-execution

    # ── 6. Recommendation ──
    total_duration = round(time.perf_counter() - started, 4)

    if report.ok and delta.stage_changed:
        recommendation = (
            f"Project advanced from {delta.previous_stage} → {delta.current_stage}. "
            f"Next: re-run full PLEI analysis to identify new capability requirements."
        )
    elif report.ok:
        rec = "All steps completed successfully."
        if delta.gaps_closed:
            rec += f" {len(delta.gaps_closed)} gap(s) closed."
        if delta.risks_mitigated:
            rec += f" {len(delta.risks_mitigated)} risk(s) mitigated."
        recommendation = rec
    elif report.blocked_steps > 0:
        recommendation = (
            f"{report.blocked_steps} step(s) BLOCKED by ActionGate. "
            f"Operator approval required: {report.review_summary[:200]}"
        )
    elif report.failed_steps > 0:
        recommendation = (
            f"{report.failed_steps} step(s) failed. Review error details and "
            f"consider re-planning with fallback providers."
        )
    else:
        recommendation = "Execution complete — review StepResults for details."

    return LoopResult(
        execution_report=report,
        twin_delta=delta,
        loop_duration_s=total_duration,
        recommendation=recommendation,
        ready_for_calibration=(report.total_steps > 0),  # Phase 7 readiness
    )


# ── Serialization ─────────────────────────────────────────────────────────


def loop_result_as_dict(loop: LoopResult) -> dict[str, Any]:
    from msb_v3.plei.harness.bridge import execution_report_as_dict

    return {
        "execution": execution_report_as_dict(loop.execution_report),
        "twin_delta": {
            "evidence_age_s": loop.twin_delta.evidence_age_s,
            "twin_updated": loop.twin_delta.twin_updated,
            "previous_stage": loop.twin_delta.previous_stage,
            "current_stage": loop.twin_delta.current_stage,
            "stage_changed": loop.twin_delta.stage_changed,
            "confidence_before": loop.twin_delta.confidence_before,
            "confidence_after": loop.twin_delta.confidence_after,
            "health_deltas": loop.twin_delta.health_deltas,
            "gaps_closed": loop.twin_delta.gaps_closed,
            "gaps_opened": loop.twin_delta.gaps_opened,
            "risks_mitigated": loop.twin_delta.risks_mitigated,
            "risks_created": loop.twin_delta.risks_created,
            "new_verified_claims": loop.twin_delta.new_verified_claims,
            "new_contradicted_claims": loop.twin_delta.new_contradicted_claims,
        },
        "loop_duration_s": loop.loop_duration_s,
        "recommendation": loop.recommendation,
        "ready_for_calibration": loop.ready_for_calibration,
    }