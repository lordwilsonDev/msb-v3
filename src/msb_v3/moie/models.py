"""MoIE data model (spec §3, §23-25).

The unit of analysis is an ExpertReport per expert; the unit of decision is
a MoIEDecision from the meta-critic. Every verdict is one of SAFE /
CONCERN / BLOCK — the meta-critic is fail-closed (any BLOCK blocks).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class Assumption:
    """One assumption extracted from the claim, with its inversion.

    ``inverted`` is the counterfactual: "what if this is wrong?". ``risk``
    is a plain-language consequence if the assumption fails.
    """

    text: str
    kind: str  # "explicit" | "implicit"
    source: str  # expert_id that surfaced it
    confidence: float = 0.5  # 0..1, how load-bearing it is
    inverted: str = ""
    risk: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "kind": self.kind,
            "source": self.source,
            "confidence": round(self.confidence, 2),
            "inverted": self.inverted,
            "risk": self.risk,
        }


@dataclass
class ExpertReport:
    """One expert's complete inversion analysis of the claim."""

    expert_id: str
    expert_name: str
    verdict: str  # SAFE | CONCERN | BLOCK
    confidence: float  # 0..1 in the expert's own analysis
    assumptions: List[Assumption] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)
    mitigations: List[str] = field(default_factory=list)
    falsifiable_predictions: List[str] = field(default_factory=list)
    causal_alternatives: List[str] = field(default_factory=list)
    evidence_hits: List[str] = field(default_factory=list)  # memory-fabric ids
    summary: str = ""
    model: str = ""  # reviewer model identity (LLM-backed experts only)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "expert_id": self.expert_id,
            "expert_name": self.expert_name,
            "verdict": self.verdict,
            "confidence": round(self.confidence, 2),
            "assumptions": [a.as_dict() for a in self.assumptions],
            "risks": self.risks,
            "mitigations": self.mitigations,
            "falsifiable_predictions": self.falsifiable_predictions,
            "causal_alternatives": self.causal_alternatives,
            "evidence_hits": self.evidence_hits,
            "summary": self.summary,
            "model": self.model,
        }


@dataclass
class Contradiction:
    """A material disagreement between two experts on one axis."""

    axis: str
    expert_a: str
    expert_b: str
    a_says: str
    b_says: str
    material: bool = True

    def as_dict(self) -> Dict[str, Any]:
        return {
            "axis": self.axis,
            "expert_a": self.expert_a,
            "expert_b": self.expert_b,
            "a_says": self.a_says,
            "b_says": self.b_says,
            "material": self.material,
        }


@dataclass
class IDS:
    """Inversion Depth Score (spec §23) — measures whether the inversion
    actually *did* anything, not whether it produced prose."""

    assumptions_extracted: int = 0
    assumptions_inverted: int = 0
    evidence_retrieved: int = 0
    contradictions_found: int = 0
    causal_alternatives: int = 0
    adversarial_critiques: int = 0
    falsifiable_predictions: int = 0
    depth_score: float = 0.0  # weighted composite 0..1

    def as_dict(self) -> Dict[str, Any]:
        return {
            "assumptions_extracted": self.assumptions_extracted,
            "assumptions_inverted": self.assumptions_inverted,
            "evidence_retrieved": self.evidence_retrieved,
            "contradictions_found": self.contradictions_found,
            "causal_alternatives": self.causal_alternatives,
            "adversarial_critiques": self.adversarial_critiques,
            "falsifiable_predictions": self.falsifiable_predictions,
            "depth_score": round(self.depth_score, 3),
        }


@dataclass
class MoIEDecision:
    """The final meta-critic decision for one claim.

    ``verdict``: APPROVE | CONDITIONAL | BLOCK. ``blocked`` is the §25
    inversion-gate surface: a consumer (e.g. the Governor) treats
    ``blocked=True`` as "do not execute as planned".
    """

    claim: str
    verdict: str  # APPROVE | CONDITIONAL | BLOCK
    confidence: float  # 0..1, degraded by material contradictions
    reports: List[ExpertReport] = field(default_factory=list)
    contradictions: List[Contradiction] = field(default_factory=list)
    assumptions: List[Assumption] = field(default_factory=list)
    recommended_actions: List[str] = field(default_factory=list)
    meta_critique: str = ""
    ids: IDS = field(default_factory=IDS)

    @property
    def blocked(self) -> bool:
        return self.verdict == "BLOCK"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "claim": self.claim,
            "verdict": self.verdict,
            "blocked": self.blocked,
            "confidence": round(self.confidence, 2),
            "experts": [r.as_dict() for r in self.reports],
            "contradictions": [c.as_dict() for c in self.contradictions],
            "assumptions": [a.as_dict() for a in self.assumptions],
            "recommended_actions": self.recommended_actions,
            "meta_critique": self.meta_critique,
            "ids": self.ids.as_dict(),
        }
