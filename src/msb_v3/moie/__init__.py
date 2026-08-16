"""MoIE — Mixture of Inversion Experts (Sovereign Architecture v4.0 §3,
§23-25; Phase 3 spec §31 items 18-24).

AIL asks *what assumptions are we accepting without question?*; MoIE
answers it with *adversarial diversity*: a router selects experts (security,
architecture, economic, operational, reliability, governance, adversarial,
human-factor, data/memory, domain), each expert extracts + inverts the
claim's assumptions, an evidence merger grounds the analysis (best-effort
memory-fabric recall), a contradiction detector finds material
disagreements, and a meta-critic produces the final decision.

The meta-critic is fail-closed (a BLOCK from any expert blocks the
decision) and every decision carries an Inversion Depth Score (IDS, §23) —
assumptions extracted/inverted, evidence retrieved, contradictions found,
causal alternatives, adversarial critiques, falsifiable predictions — so
AIL improves decisions instead of generating philosophical prose.

Everything is deterministic by default (rule-based experts over the claim
text); the expert interface is pluggable so an LLM-backed domain expert can
be injected later without touching the controller.
"""

from msb_v3.moie.engine import MoIEController
from msb_v3.moie.experts import Expert, ExpertRegistry
from msb_v3.moie.llm_experts import LLMExpert, ReviewPanel, build_diverse_reviewer_panel
from msb_v3.moie.models import (
    IDS,
    Assumption,
    Contradiction,
    ExpertReport,
    MoIEDecision,
)

__all__ = [
    "Assumption",
    "Contradiction",
    "Expert",
    "ExpertReport",
    "ExpertRegistry",
    "IDS",
    "LLMExpert",
    "MoIEController",
    "MoIEDecision",
    "ReviewPanel",
    "build_diverse_reviewer_panel",
]
