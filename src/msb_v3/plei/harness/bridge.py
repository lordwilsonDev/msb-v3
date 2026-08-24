"""Harness Bridge — execute a PLEI WorkPlan through governed providers.

The bridge is the execution arm of PLEI's decision engine. It takes a
WorkPlan (from work_plan.py), gates each step through the ActionGate,
executes via the 10-provider seam, verifies claims through MoIE, and logs
every decision into the evidence spine.

Flow per step:
    1. ActionGate.gate(capability, risk_tier, tainted_inputs) → SAFE/REVIEW/BLOCK
    2. Provider.execute(goal) → ProviderResult (ok, output, artifacts, error)
    3. MoIE Controller.analyze(verification_claims) → MoIEDecision
    4. Evidence spine.append(step record) → hash-chained

Fail-closed: a BLOCKED step halts the plan. A REVIEW step returns a
REVIEW signal so the caller can escalate. A failed execution retries
the fallback provider chain.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from msb_v3.plei.harness.work_plan import WorkPlan, WorkPlanStep

logger = logging.getLogger(__name__)


# ── Result types ──────────────────────────────────────────────────────────


@dataclass(slots=True)
class StepResult:
    """The result of executing a single WorkPlanStep."""

    step_id: str
    sequence: int
    goal: str
    provider_id: str
    ok: bool
    output: str = ""
    error: str = ""
    duration_s: float = 0.0

    # Gate
    gate_verdict: str = "SAFE"  # SAFE | REVIEW | BLOCK
    gate_reason: str = ""

    # Verification (MoIE)
    claims_verified: int = 0
    claims_total: int = 0
    verified: bool = False

    # Artifacts
    artifacts: dict[str, Any] = field(default_factory=dict)

    # Fallback
    fallback_tried: list[str] = field(default_factory=list)
    fallback_succeeded: bool = False


@dataclass(slots=True)
class ExecutionReport:
    """Complete execution report for one WorkPlan."""

    plan_id: str
    ok: bool  # did ALL steps complete without BLOCK or unrecoverable failure?
    total_steps: int
    completed_steps: int
    failed_steps: int
    blocked_steps: int
    review_steps: int

    step_results: list[StepResult] = field(default_factory=list)

    total_duration_s: float = 0.0
    evidence_receipts: list[str] = field(default_factory=list)

    # For REVIEW cases — what requires human attention
    review_summary: str = ""


# ── Gate helpers ──────────────────────────────────────────────────────────


def _gate_step(
    step: WorkPlanStep,
    *,
    gate: Any | None = None,
    approved_capabilities: set[str] | None = None,
) -> tuple[str, str]:
    """Gate one step through the ActionGate.

    Returns (verdict, reason). If no gate is available, defaults to SAFE
    with a note — the bridge still executes, just without gating.
    """
    if gate is None:
        return ("SAFE", "no ActionGate available — running ungated")

    try:
        # Map step properties to gate parameters
        capability = step.capabilities_required[0] if step.capabilities_required else "read_vault"
        verdict = gate.gate(
            capability,
            tainted_inputs=step.tainted_inputs,
            approved=approved_capabilities,
        )
        return (verdict.verdict if hasattr(verdict, "verdict") else str(verdict),
                verdict.reason if hasattr(verdict, "reason") else "")
    except Exception as exc:
        logger.warning("ActionGate exception for step %s: %s", step.step_id, exc)
        return ("BLOCK", f"gate exception: {exc}")


# ── Provider execution ───────────────────────────────────────────────────


async def _execute_with_provider(
    step: WorkPlanStep,
    *,
    provider_id: str,
    providers_by_id: dict[str, Any],
    session: str = "default",
) -> tuple[bool, str, dict[str, Any], str]:
    """Execute one step through a named provider.

    Returns (ok, output, artifacts, error).
    """
    provider = providers_by_id.get(provider_id)
    if provider is None:
        return False, "", {}, f"provider '{provider_id}' not found in registry"

    if not provider.available():
        return False, "", {}, f"provider '{provider_id}' is unavailable: {provider.unavailable_reason()}"

    try:
        result = await provider.execute(
            goal=step.goal,
            context={"step_id": step.step_id, "plan_step": step.sequence},
            session=session,
        )
        return result.ok, result.output, result.artifacts or {}, result.error or ""
    except Exception as exc:
        logger.warning("Provider %s failed for step %s: %s", provider_id, step.step_id, exc)
        return False, "", {}, str(exc)


# ── MoIE verification ────────────────────────────────────────────────────


def _verify_claims(
    claims: list[str],
    *,
    moie: Any | None = None,
) -> tuple[int, int, bool]:
    """Run MoIE verification on the step's claims.

    Returns (verified_count, total, all_passed).
    """
    if not claims:
        return 0, 0, True  # nothing to verify → passes

    if moie is None:
        # No MoIE available — mark all as unverified
        return 0, len(claims), False

    verified = 0
    for claim in claims:
        try:
            decision = moie.analyze(claim)
            if getattr(decision, "verdict", "") in ("OK", "PASS"):
                verified += 1
        except Exception as exc:
            logger.warning("MoIE verification failed for claim %r: %s", claim[:80], exc)

    return verified, len(claims), verified == len(claims)


# ── Main bridge ──────────────────────────────────────────────────────────


async def execute_plan(
    plan: WorkPlan,
    *,
    providers_by_id: dict[str, Any] | None = None,
    gate: Any | None = None,
    moie: Any | None = None,
    evidence_spine: Any | None = None,
    approved_capabilities: set[str] | None = None,
    session: str = "default",
) -> ExecutionReport:
    """Execute a complete WorkPlan through governed providers.

    This is the primary entry point for Phase 6. Each step goes through:
        Gate → Provider (with fallback) → MoIE verify → Evidence spine.

    The plan halts on the first BLOCK verdict. REVIEW steps are collected
    but execution continues so the report captures everything that needs
    attention.
    """
    started = time.perf_counter()
    step_results: list[StepResult] = []
    blocked = 0
    review_steps = 0
    failed = 0
    completed = 0
    receipts: list[str] = []
    review_msgs: list[str] = []

    approved = approved_capabilities or set()

    for step in sorted(plan.steps, key=lambda s: s.sequence):
        # ── Gate ──
        verdict, reason = _gate_step(
            step,
            gate=gate,
            approved_capabilities=approved,
        )

        if verdict == "BLOCK":
            blocked += 1
            step_results.append(StepResult(
                step_id=step.step_id,
                sequence=step.sequence,
                goal=step.goal,
                provider_id=step.preferred_provider_id,
                ok=False,
                error=f"BLOCKED: {reason}",
                gate_verdict="BLOCK",
                gate_reason=reason,
            ))
            break  # BLOCK halts the plan

        if verdict == "REVIEW":
            review_steps += 1
            review_msgs.append(f"[{step.step_id}] REVIEW: {reason}")

        # ── Execute with fallback chain ──
        provider_ids = [step.preferred_provider_id] + step.fallback_provider_ids
        provider_ids = [p for p in provider_ids if p]  # filter empties

        prov_ok = False
        prov_output = ""
        prov_artifacts: dict[str, Any] = {}
        prov_error = ""
        tried: list[str] = []
        used_provider = ""

        if providers_by_id:
            for pid in provider_ids:
                tried.append(pid)
                prov_ok, prov_output, prov_artifacts, prov_error = (
                    await _execute_with_provider(
                        step,
                        provider_id=pid,
                        providers_by_id=providers_by_id,
                        session=session,
                    )
                )
                if prov_ok:
                    used_provider = pid
                    break
        else:
            prov_error = "no providers available (providers_by_id is None)"
            tried = provider_ids

        # ── MoIE verify ──
        verified_count, claims_total, all_verified = _verify_claims(
            step.verification_claims,
            moie=moie,
        )

        # ── Evidence spine ──
        if evidence_spine is not None:
            try:
                from msb_v3.evidence.spine import DecisionEvidence

                ev = DecisionEvidence(
                    task_id=step.step_id,
                    policy_version="plei.harness",
                    policy_result="PASS" if prov_ok else "FAIL",
                    risk_level=str(step.risk_tier),
                    execution_id=step.step_id,
                    provider=used_provider or step.preferred_provider_id,
                )
                record = evidence_spine.append(ev)
                if hasattr(record, "decision_id"):
                    receipts.append(record.decision_id)
            except Exception as exc:
                logger.warning("Evidence spine write failed for step %s: %s", step.step_id, exc)

        # ── Record result ──
        if prov_ok:
            completed += 1
        else:
            failed += 1

        sr = StepResult(
            step_id=step.step_id,
            sequence=step.sequence,
            goal=step.goal,
            provider_id=used_provider or step.preferred_provider_id,
            ok=prov_ok,
            output=prov_output[:500] if prov_output else "",
            error=prov_error,
            duration_s=0.0,  # per-step timing not instrumented yet
            gate_verdict=verdict,
            gate_reason=reason,
            claims_verified=verified_count,
            claims_total=claims_total,
            verified=all_verified,
            artifacts=prov_artifacts,
            fallback_tried=[p for p in tried if p != used_provider],
            fallback_succeeded=prov_ok and len(tried) > 1,
        )
        step_results.append(sr)

        # Don't continue if the step failed and there's no fallback
        if not prov_ok and review_steps == 0 and blocked == 0:
            # failed step with no REVIEW/BLOCK — continue trying remaining steps
            # but mark the plan as not-ok
            pass

    total_duration = round(time.perf_counter() - started, 4)

    return ExecutionReport(
        plan_id=plan.plan_id,
        ok=(blocked == 0 and failed == 0),
        total_steps=len(plan.steps),
        completed_steps=completed,
        failed_steps=failed,
        blocked_steps=blocked,
        review_steps=review_steps,
        step_results=step_results,
        total_duration_s=total_duration,
        evidence_receipts=receipts,
        review_summary=" | ".join(review_msgs) if review_msgs else "",
    )


# ── Serialization ─────────────────────────────────────────────────────────


def execution_report_as_dict(report: ExecutionReport) -> dict[str, Any]:
    return {
        "plan_id": report.plan_id,
        "ok": report.ok,
        "total_steps": report.total_steps,
        "completed_steps": report.completed_steps,
        "failed_steps": report.failed_steps,
        "blocked_steps": report.blocked_steps,
        "review_steps": report.review_steps,
        "total_duration_s": report.total_duration_s,
        "evidence_receipts": report.evidence_receipts,
        "review_summary": report.review_summary,
        "step_results": [
            {
                "step_id": s.step_id,
                "sequence": s.sequence,
                "goal": s.goal[:200],
                "provider_id": s.provider_id,
                "ok": s.ok,
                "error": s.error[:300] if s.error else "",
                "gate_verdict": s.gate_verdict,
                "gate_reason": s.gate_reason[:200],
                "claims_verified": f"{s.claims_verified}/{s.claims_total}",
                "verified": s.verified,
                "fallback_tried": s.fallback_tried,
                "fallback_succeeded": s.fallback_succeeded,
            }
            for s in report.step_results
        ],
    }