"""Wrongness Engine MVP — the epistemic termination protocol, mechanized.

Sits above architecture claims and systematically tries to falsify them
(doc 30_Architecture/Wrongness-Engine/00_Acknowledgment.md, §VI–VII).

MVP scope (deliberately lean, §V anti-architecture-theater):
  - deterministic, stdlib-only checks first (the by-hand run showed the
    strongest check is often 5 lines of shell — call-site count, stat mode,
    tracked-path coverage, porcelain state);
  - three-tier escalation policy (ESCALATE / CHECK / NOTE) — the load-bearing
    design constraint from the retrospective: investigation-prompts route to
    CHECK, never ESCALATE;
  - a machine corpus of the by-hand 21-decision experiment the MVP must
    reproduce (PEDR 1.0, FP 16.7% / 28.6%);
  - the specialist-fleet bake-off as the first live claim.

No LLM, no Qdrant, no new dependencies — those earn their place (§V) only if
they beat this deterministic baseline. See README in this package.
"""

from __future__ import annotations

from .checks import EvidenceLink
from .engine import WrongnessEngine, run_claim, run_replay, run_score
from .policy import CHECK, CONFLICTING, ESCALATE, NOTE, tier_for_class, urgency_score
from .report import render_report

__all__ = [
    "CHECK",
    "CONFLICTING",
    "ESCALATE",
    "EvidenceLink",
    "NOTE",
    "WrongnessEngine",
    "__version__",
    "render_report",
    "run_claim",
    "run_replay",
    "run_score",
    "tier_for_class",
    "urgency_score",
]

__version__ = "0.1.0"
