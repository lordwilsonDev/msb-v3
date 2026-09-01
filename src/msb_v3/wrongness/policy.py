"""The escalation policy — the load-bearing design constraint.

The by-hand retrospective's mandatory MVP constraint: **the escalation
policy, not the attack passes, is where the engine lives or dies.**

Verdict states (4 — the field-standard AVeriTeC categories mapped onto the
escalation policy; added CONFLICTING per ``03_Inversion-Audit.md`` M2):

- ``ESCALATE``    — an evidence-backed failure-assertion (block / human decides)
- ``CONFLICTING`` — evidence points BOTH ways (supporting evidence stands AND
                    a refuting signal fires) — human decides, above CHECK
- ``CHECK``       — an investigation prompt / UNKNOWN (route below escalation)
- ``NOTE``        — below escalation, informational / confirmed

Rubric (M4 — the explicit assignment rules the audit found missing):

1. ESCALATE requires an evidence-backed failure-assertion: a deterministic
   check that fails, or a recorded failure-assertion in corpus replay.
   A count of passes agreeing at CHECK never escalates on its own — pass
   consensus is routing evidence, not falsification evidence (the A4-A6
   lesson, test-enforced).
2. CONFLICTING requires BOTH a confirming signal (NOTE-tier finding —
   a passing deterministic check or recorded supporting evidence) AND a
   refuting signal (ESCALATE-tier).  Without the confirming signal it is
   plain ESCALATE; without the refuting signal it is NOTE/CHECK.
3. Investigation-prompts must NEVER escalate on their own — that single
   design decision moved FP from 28.6% to 16.7% in the by-hand corpus.
4. Urgency = severity (tier) x consequence (claim field).  A high-consequence
   claim at CHECK outranks a low-consequence claim at ESCALATE on the
   urgency scale even though the verdict strings differ.
"""

from __future__ import annotations

from .claims import Finding

ESCALATE = "ESCALATE"
CONFLICTING = "CONFLICTING"
CHECK = "CHECK"
NOTE = "NOTE"

_TIER_ORDER = {NOTE: 0, CHECK: 1, CONFLICTING: 2, ESCALATE: 3}

CONSEQUENCE_WEIGHT = {"low": 0.5, "medium": 1.0, "high": 2.0}


def tier_for_class(escalation_class: str | None) -> str:
    """Map a recorded escalation class to a tier (corpus replay only)."""
    if escalation_class == "failure-assertion":
        return ESCALATE
    if escalation_class == "investigation-prompt":
        return CHECK
    return NOTE


def _has_refuting(findings: list[Finding]) -> bool:
    return any(f.tier == ESCALATE for f in findings)


def _has_confirming(findings: list[Finding]) -> bool:
    # A NOTE-tier finding is a confirming signal: it only arises from a
    # passing deterministic check or recorded supporting evidence.
    return any(f.tier == NOTE for f in findings)


def claim_verdict(findings: list[Finding]) -> str:
    """Aggregate per-finding tiers into a claim verdict.

    CONFLICTING when evidence points both ways; otherwise the max tier
    (ESCALATE > CONFLICTING > CHECK > NOTE).
    """
    if not findings:
        return NOTE
    if _has_refuting(findings) and _has_confirming(findings):
        return CONFLICTING
    return max((f.tier for f in findings), key=lambda t: _TIER_ORDER[t])


def urgency_score(findings: list[Finding], consequence: str = "low") -> float:
    """Severity x consequence urgency in [0, 1] (M4).

    severity = top tier / 3 (0..1), multiplied by the consequence weight,
    capped at 1.0.  A high-consequence CHECK (2/3) outranks a low-consequence
    ESCALATE (0.5) on this scale.
    """
    if not findings:
        return 0.0
    top = max((_TIER_ORDER.get(f.tier, 0) for f in findings), default=0)
    weight = CONSEQUENCE_WEIGHT.get(consequence, 1.0)
    return min(1.0, (top / 3.0) * weight)


def passes_agreeing(findings: list[Finding], tier: str) -> list[str]:
    """Distinct passes emitting ``tier`` — the consensus view (M4).

    Consensus is routing evidence: it tells a human how many independent
    angles converged.  Per rubric rule 1 it does NOT by itself escalate.
    """
    return sorted({f.pass_name for f in findings if f.tier == tier})


def findings_to_tier_map(findings: list[Finding]) -> dict[str, str]:
    return {f.pass_name: f.tier for f in findings}
