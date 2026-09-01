"""Claim, check, and finding models for the Wrongness Engine MVP.

A ``Claim`` is a falsifiable statement plus the conditions that would
disprove it — the smallest unit the engine operates on.  A ``CheckSpec``
names a deterministic falsification action (the "5 lines of shell" power
the by-hand run found most valuable).  A ``Finding`` is one pass's output
on a claim, carrying an escalation tier.

Verdict states (4, per the field standard AVeriTeC categories mapped onto
the escalation policy — see ``policy.py``): a claim may be confirmed
(``NOTE``), under-investigated (``CHECK``), refuted (``ESCALATE``), or
carry evidence pointing both ways (``CONFLICTING``).

Fields added by the inversion audit (``03_Inversion-Audit.md`` M1/M2/M4):
- ``checks`` — a claim can carry several falsification gates, not one;
- ``supporting_evidence`` — recorded evidence FOR the claim, so a refuting
  check can produce a CONFLICTING verdict instead of collapsing to max;
- ``conflicts_with`` — corpus metadata marking paired claims whose
  evidence contradicts (e.g. B1/B2, "100% CLOSED" vs the 83% reset);
- ``consequence`` — low/medium/high, feeding the severity x consequence
  urgency score (M4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CheckSpec:
    """A deterministic falsification action, e.g. ``file_mode .env == 0600``."""

    kind: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Finding:
    """One pass's output on a claim: what to check, at what escalation tier."""

    pass_name: str
    tier: str
    statement: str
    evidence: str | None = None


@dataclass(frozen=True)
class Claim:
    """A falsifiable statement the engine attempts to attack."""

    id: str
    statement: str
    domain: str
    falsification_conditions: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    # Corpus-only fields (absent for live claims).  ``escalation_class``
    # records how the by-hand run routed the claim: investigation-prompts
    # (A4-A6 in the retrospective) route to CHECK, never ESCALATE.
    escalation_class: str | None = None  # "failure-assertion" | "investigation-prompt"
    strongest_pass: str | None = None  # the pass the by-hand run credited
    outcome: str | None = None  # "HIT" | "FP" | "CORRECT" | "—"
    hit_weight: int = 1  # A8 carried two latent hazards (weight 2)
    checks: tuple[CheckSpec, ...] = ()  # deterministic falsification gates
    supporting_evidence: tuple[str, ...] = ()  # recorded evidence FOR the claim
    conflicts_with: tuple[str, ...] = ()  # paired claims with contradicting evidence
    consequence: str = "low"  # "low" | "medium" | "high" — urgency input (M4)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Claim":
        checks = d.get("checks")
        if checks is None and d.get("check"):
            checks = [d["check"]]  # legacy single-check form
        return cls(
            id=str(d["id"]),
            statement=str(d["statement"]),
            domain=str(d.get("domain", "")),
            falsification_conditions=tuple(d.get("falsification_conditions", [])),
            evidence_refs=tuple(d.get("evidence_refs", [])),
            escalation_class=d.get("escalation_class"),
            strongest_pass=d.get("strongest_pass"),
            outcome=d.get("outcome"),
            hit_weight=int(d.get("hit_weight", 1)),
            checks=tuple(
                CheckSpec(kind=str(c["kind"]), params=dict(c.get("params", {})))
                for c in (checks or [])
            ),
            supporting_evidence=tuple(d.get("supporting_evidence", [])),
            conflicts_with=tuple(d.get("conflicts_with", [])),
            consequence=str(d.get("consequence", "low")),
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "statement": self.statement,
            "domain": self.domain,
            "falsification_conditions": list(self.falsification_conditions),
            "evidence_refs": list(self.evidence_refs),
        }
        if self.escalation_class:
            d["escalation_class"] = self.escalation_class
        if self.strongest_pass:
            d["strongest_pass"] = self.strongest_pass
        if self.outcome:
            d["outcome"] = self.outcome
        if self.hit_weight != 1:
            d["hit_weight"] = self.hit_weight
        if self.checks:
            d["checks"] = [{"kind": c.kind, "params": c.params} for c in self.checks]
        if self.supporting_evidence:
            d["supporting_evidence"] = list(self.supporting_evidence)
        if self.conflicts_with:
            d["conflicts_with"] = list(self.conflicts_with)
        if self.consequence != "low":
            d["consequence"] = self.consequence
        return d
