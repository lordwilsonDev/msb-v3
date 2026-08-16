"""Software Factory independent reviewer (spec §4.2.6 — review stage; §9).

The review is **independent by construction**: it never reads the
builder's own summary. It inverts the change with MoIE and, when a code
graph is available, checks the blast radius of the changed symbols. A
BLOCK here blocks the factory verdict — the anti-fabrication gate.

The reviewer seam accepts any MoIE controller: the deterministic
rule-based one (default) or a diverse LLM ``ReviewPanel`` controller whose
experts run concurrently via ``areview`` (see ``moie.llm_experts``). The
panel's distinct reviewer models are recorded on the ``Review`` so the
evidence chain carries *who* reviewed *with which model*.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from msb_v3.factory.models import BuildResult, Plan, Review, ReviewFinding
from msb_v3.moie import MoIEController


def _claim(plan: Plan, build: BuildResult) -> str:
    return f"{plan.goal}\n\nchanged files: {', '.join(build.changed_files[:10])}"


def _context(high_impact: bool, build: BuildResult) -> dict:
    # high_impact comes from the issue's severity; the diff + changed files
    # are what an LLM-backed reviewer actually reads (deterministic experts
    # ignore the extra keys).
    return {"high_impact": high_impact, "changed_files": build.changed_files, "diff": build.diff}


def _run_moie_sync(moie: Any, plan: Plan, build: BuildResult, high_impact: bool):
    moie = moie or MoIEController()
    try:
        return moie.analyze(_claim(plan, build), context=_context(high_impact, build))
    except Exception:  # noqa: BLE001 — a broken MoIE degrades the review, never fakes it
        return None


async def _run_moie_async(moie: Any, plan: Plan, build: BuildResult, high_impact: bool):
    moie = moie or MoIEController()
    try:
        if hasattr(moie, "aanalyze"):
            return await moie.aanalyze(_claim(plan, build), context=_context(high_impact, build))
        return await asyncio.to_thread(moie.analyze, _claim(plan, build), context=_context(high_impact, build))
    except Exception:  # noqa: BLE001 — a broken MoIE degrades the review, never fakes it
        return None


def _review_from_decision(
    plan: Plan,
    build: BuildResult,
    decision: Any,
    *,
    codegraph: Any = None,
    repo: Optional[str] = None,
) -> Review:
    findings: list[ReviewFinding] = []

    if not build.ok:
        findings.append(ReviewFinding("blocker", f"build failed: {build.error or 'no error detail'}"))
        return Review(verdict="BLOCK", findings=findings, independent=True)

    if not build.changed_files:
        findings.append(ReviewFinding("blocker", "no files changed — the builder made no modification"))

    # Independent inversion of the change (MoIE), never the builder's claim.
    moie_verdict = ""
    moie_ids = 0.0
    reviewer_models: list[str] = []
    if decision is None:
        findings.append(ReviewFinding("concern", "MoIE inversion unavailable — independent review is partial"))
    else:
        moie_verdict = decision.verdict
        moie_ids = decision.ids.depth_score
        for report in getattr(decision, "reports", []) or []:
            model = getattr(report, "model", "")
            if model and model not in reviewer_models:
                reviewer_models.append(model)
        if decision.blocked:
            findings.append(ReviewFinding("blocker", f"MoIE blocked the change: {decision.meta_critique[:200]}"))
        elif decision.verdict == "CONDITIONAL":
            if decision.recommended_actions:
                for action in decision.recommended_actions[:3]:
                    findings.append(ReviewFinding("concern", f"MoIE: {action}"))
            else:
                findings.append(ReviewFinding("concern", f"MoIE: {decision.meta_critique[:200]}"))

    # Blast-radius check via the code graph (best-effort).
    if codegraph is not None and repo:
        try:
            for change in build.changed_files[:8]:
                hits = codegraph.find_symbol(repo, change, limit=3)
                for hit in hits:
                    findings.append(
                        ReviewFinding("info", f"changed symbol {hit['fq_name']} @ {hit['file']}:{hit['line']} — verify its callers")
                    )
        except Exception:  # noqa: BLE001
            pass

    if any(f.severity == "blocker" for f in findings):
        verdict = "BLOCK"
    elif any(f.severity == "concern" for f in findings):
        verdict = "CONCERN"
    else:
        verdict = "APPROVE"
    if not findings and build.changed_files:
        findings.append(ReviewFinding("info", "no blocker or concern surfaced; independent review approves on current evidence"))

    return Review(
        verdict=verdict,
        findings=findings[:12],
        moie_verdict=moie_verdict,
        moie_ids=moie_ids,
        independent=True,
        reviewer_models=reviewer_models,
    )


def review(
    plan: Plan,
    build: BuildResult,
    *,
    moie: Optional[Any] = None,
    codegraph: Any = None,
    repo: Optional[str] = None,
    high_impact: bool = False,
) -> Review:
    """Synchronous independent review (deterministic MoIE by default)."""
    decision = _run_moie_sync(moie, plan, build, high_impact)
    return _review_from_decision(plan, build, decision, codegraph=codegraph, repo=repo)


async def areview(
    plan: Plan,
    build: BuildResult,
    *,
    moie: Optional[Any] = None,
    codegraph: Any = None,
    repo: Optional[str] = None,
    high_impact: bool = False,
) -> Review:
    """Concurrent independent review — experts (e.g. a diverse LLM panel)
    run in parallel instead of serializing their latency."""
    decision = await _run_moie_async(moie, plan, build, high_impact)
    return _review_from_decision(plan, build, decision, codegraph=codegraph, repo=repo)
