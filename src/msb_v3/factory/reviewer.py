"""Software Factory independent reviewer (spec §4.2.6 — review stage; §9).

The review is **independent by construction**: it never reads the
builder's own summary. It inverts the change with MoIE and, when a code
graph is available, checks the blast radius of the changed symbols. A
BLOCK here blocks the factory verdict — the anti-fabrication gate.
"""

from __future__ import annotations

from typing import Any, Optional

from msb_v3.factory.models import BuildResult, Plan, Review, ReviewFinding
from msb_v3.moie import MoIEController


def review(
    plan: Plan,
    build: BuildResult,
    *,
    moie: Optional[MoIEController] = None,
    codegraph: Any = None,
    repo: Optional[str] = None,
    high_impact: bool = False,
) -> Review:
    findings: list[ReviewFinding] = []

    if not build.ok:
        findings.append(ReviewFinding("blocker", f"build failed: {build.error or 'no error detail'}"))
        return Review(verdict="BLOCK", findings=findings, independent=True)

    if not build.changed_files:
        findings.append(ReviewFinding("blocker", "no files changed — the builder made no modification"))

    # Independent inversion of the change (MoIE), never the builder's claim.
    # high_impact comes from the issue's severity — a benign change is not
    # escalated, a critical one leans fail-closed.
    moie_verdict = ""
    moie_ids = 0.0
    try:
        decision = (moie or MoIEController()).analyze(
            f"{plan.goal}\n\nchanged files: {', '.join(build.changed_files[:10])}",
            context={"high_impact": high_impact},
        )
        moie_verdict = decision.verdict
        moie_ids = decision.ids.depth_score
        if decision.blocked:
            findings.append(ReviewFinding("blocker", f"MoIE blocked the change: {decision.meta_critique[:200]}"))
        elif decision.verdict == "CONDITIONAL":
            if decision.recommended_actions:
                for action in decision.recommended_actions[:3]:
                    findings.append(ReviewFinding("concern", f"MoIE: {action}"))
            else:
                findings.append(ReviewFinding("concern", f"MoIE: {decision.meta_critique[:200]}"))
    except Exception:  # noqa: BLE001 — a broken MoIE degrades the review, never fakes it
        findings.append(ReviewFinding("concern", "MoIE inversion unavailable — independent review is partial"))

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

    return Review(verdict=verdict, findings=findings[:12], moie_verdict=moie_verdict, moie_ids=moie_ids, independent=True)
