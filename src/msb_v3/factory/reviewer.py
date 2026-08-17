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

Deterministic coherence scan (2026-08-17): on top of whichever MoIE
controller runs, the change's own text is scanned for internal
self-contradictions (a claim both asserted and negated). This runs on
EVERY review regardless of reviewer model — a weak LLM approving a
self-contradictory doc is exactly the failure the live dogfood exposed,
and no model (strong or weak) should be the only thing standing between a
contradiction and the evidence chain.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Optional

from msb_v3.factory.models import BuildResult, Plan, Review, ReviewFinding
from msb_v3.moie import MoIEController

# Deterministic coherence scan: a small set of action verbs the scan looks
# for in BOTH asserted and negated form inside the same change. A verb that
# appears both ways is a likely internal contradiction (e.g. "no file
# written" in one section and "vault note written" in another).
_COHERENCE_VERBS = (
    "write", "wrote", "written",
    "create", "created", "delete", "deleted", "remove", "removed",
    "run", "ran", "execute", "executed", "occurred", "happened",
)

# Negation cues that make a verb claim negative. Kept conservative so the
# scan flags only clear negations, never "not" inside a word like "notable".
_NEGATION_RE = re.compile(
    r"\b(?:no|not|never|without|did\s+not|didn't|does\s+not|doesn't|no\s+\w+\s+\w+)\b",
    re.IGNORECASE,
)


def scan_doc_contradictions(diff: str) -> list[ReviewFinding]:
    """Deterministic self-contradiction scan over the change's diff text.

    For every coherence verb, a verb claim is NEGATIVE if it appears within
    4 words of a negation cue, POSITIVE otherwise. If a verb appears in both
    polarities anywhere in the change, the change asserts and denies the
    same action — a concern (not a hard block: the wording could be
    describing two different subjects, so a human/stronger model confirms).

    Runs on every review, independent of the MoIE controller, so a weak
    reviewer model cannot be the only guard between a contradiction and a
    merge.
    """
    if not diff:
        return []
    text = diff
    findings: list[ReviewFinding] = []
    for verb in _COHERENCE_VERBS:
        verb_re = re.compile(rf"\b{verb}\b", re.IGNORECASE)
        negated = False
        asserted = False
        for match in verb_re.finditer(text):
            start = max(0, match.start() - 40)
            window = text[start : match.end() + 20]
            if _NEGATION_RE.search(window):
                negated = True
            else:
                asserted = True
            if negated and asserted:
                findings.append(
                    ReviewFinding(
                        "concern",
                        f"deterministic coherence scan: the change both asserts and "
                        f"negates '{verb}' — possible internal contradiction "
                        f"(check the wording, not just the model's verdict)",
                    )
                )
                break
    return findings


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

    # Deterministic coherence scan — runs on EVERY review, independent of
    # which MoIE controller is configured. A weak LLM approving a
    # self-contradictory change must not be the only guard.
    findings.extend(scan_doc_contradictions(build.diff or ""))

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
