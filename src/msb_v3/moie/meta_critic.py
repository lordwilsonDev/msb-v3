"""MoIE meta-critic (spec §24, §25) + contradiction detector + IDS (§23).

Fail-closed: any expert BLOCK blocks the decision. Material contradictions
between experts degrade confidence (they are not consensus, they are a
reason to slow down — §9: agreement is not truth, and neither is
disagreement, but disagreement must be surfaced). The Inversion Depth
Score measures whether the inversion actually did work.
"""

from __future__ import annotations

from typing import List

from msb_v3.core.calibration import moie_calibration
from msb_v3.moie.models import (
    IDS,
    Assumption,
    Contradiction,
    ExpertReport,
    MoIEDecision,
)

# A verdict pair that is a material disagreement: the experts land far
# enough apart that one of them is likely wrong about something important.
_MATERIAL_PAIRS = {
    ("BLOCK", "SAFE"),
    ("BLOCK", "CONCERN"),
    ("CONCERN", "SAFE"),
}


def detect_contradictions(reports: List[ExpertReport]) -> List[Contradiction]:
    """Pairwise material disagreements on the risk axis.

    SAFE-vs-SAFE and identical verdicts never contradict; BLOCK vs SAFE /
    BLOCK vs CONCERN always do; CONCERN vs SAFE does when the gap is
    material — decided by confidence spread (the CONCERN expert is
    confident, the SAFE expert is too).
    """
    contradictions: List[Contradiction] = []
    seen: set[tuple] = set()
    for i, a in enumerate(reports):
        for b in reports[i + 1:]:
            pair = (a.verdict, b.verdict)
            key = tuple(sorted((a.expert_id, b.expert_id)))
            if key in seen:
                continue
            if pair in _MATERIAL_PAIRS or tuple(reversed(pair)) in _MATERIAL_PAIRS:
                if pair == ("CONCERN", "SAFE") or pair == ("SAFE", "CONCERN"):
                    # Only material when the concern is confident and the
                    # safe side is not (a weak SAFE adds no contradiction).
                    concern = a if a.verdict == "CONCERN" else b
                    if concern.confidence < moie_calibration.concern_material_min_confidence:
                        continue
                seen.add(key)
                contradictions.append(
                    Contradiction(
                        axis="overall-risk",
                        expert_a=a.expert_id,
                        expert_b=b.expert_id,
                        a_says=f"{a.expert_id} says {a.verdict} ({a.confidence:.2f})",
                        b_says=f"{b.expert_id} says {b.verdict} ({b.confidence:.2f})",
                        material=True,
                    )
                )
    return contradictions


def _ids(reports: List[ExpertReport], contradictions: List[Contradiction]) -> IDS:
    extracted = sum(len(r.assumptions) for r in reports)
    inverted = sum(1 for r in reports for a in r.assumptions if a.inverted)
    evidence = sum(len(r.evidence_hits) for r in reports)
    material = sum(1 for c in contradictions if c.material)
    alternatives = sum(len(r.causal_alternatives) for r in reports)
    critiques = sum(1 for r in reports if r.risks)  # experts that actually pushed back
    predictions = sum(len(r.falsifiable_predictions) for r in reports)

    def _norm(count: int, cap: int) -> float:
        return min(1.0, count / cap)

    depth = round(
        min(
            1.0,
            0.15 * _norm(extracted, 4)
            + 0.20 * _norm(inverted, 4)
            + 0.15 * _norm(evidence, 3)
            + 0.15 * _norm(material, 2)
            + 0.10 * _norm(alternatives, 4)
            + 0.10 * _norm(critiques, 3)
            + 0.15 * _norm(predictions, 4),
        ),
        3,
    )
    return IDS(
        assumptions_extracted=extracted,
        assumptions_inverted=inverted,
        evidence_retrieved=evidence,
        contradictions_found=material,
        causal_alternatives=alternatives,
        adversarial_critiques=critiques,
        falsifiable_predictions=predictions,
        depth_score=depth,
    )


def synthesize(
    claim: str,
    reports: List[ExpertReport],
    contradictions: List[Contradiction],
) -> MoIEDecision:
    """The meta-critic: verdict, confidence, critique, actions, IDS."""

    if any(r.verdict == "BLOCK" for r in reports):
        verdict = "BLOCK"
    elif any(r.verdict == "CONCERN" for r in reports):
        verdict = "CONDITIONAL"
    else:
        verdict = "APPROVE"

    if not reports:
        confidence = 0.0
    else:
        confidence = sum(r.confidence for r in reports) / len(reports)
        confidence -= moie_calibration.contradiction_penalty * len(contradictions)
        confidence = round(
            max(moie_calibration.confidence_min, min(moie_calibration.confidence_max, confidence)),
            2,
        )

    assumptions: List[Assumption] = []
    seen: set[str] = set()
    for r in reports:
        for a in r.assumptions:
            if a.text not in seen:
                seen.add(a.text)
                assumptions.append(a)

    actions: List[str] = []
    for r in reports:
        if r.verdict in ("BLOCK", "CONCERN"):
            for m in r.mitigations:
                if m not in actions:
                    actions.append(m)
    actions = actions[:6]

    strongest = ""
    for r in sorted(reports, key=lambda x: x.confidence, reverse=True):
        if r.risks:
            strongest = r.risks[0]
            break

    if verdict == "BLOCK":
        critique = (
            f"{len(reports)} experts assessed '{claim[:140]}'. At least one BLOCK: {strongest or 'a hard safety signal'}. "
            f"{len(contradictions)} material contradiction(s). Execution as planned is blocked."
        )
    elif verdict == "CONDITIONAL":
        critique = (
            f"{len(reports)} experts assessed '{claim[:140]}'. Strongest concern: {strongest or 'unverified assumptions'}. "
            f"{len(contradictions)} material contradiction(s). Proceed only with the listed mitigations."
        )
    else:
        critique = (
            f"{len(reports)} experts assessed '{claim[:140]}'. No BLOCK or CONCERN; "
            f"{len(assumptions)} assumption(s) inverted. Proceed — evidence of harm is absent, not guaranteed."
        )

    return MoIEDecision(
        claim=claim,
        verdict=verdict,
        confidence=confidence,
        reports=reports,
        contradictions=contradictions,
        assumptions=assumptions,
        recommended_actions=actions,
        meta_critique=critique,
        ids=_ids(reports, contradictions),
    )
