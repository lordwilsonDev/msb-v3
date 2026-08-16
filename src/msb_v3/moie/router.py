"""MoIE expert router (spec §24, §31 item 20).

The controller decides *which experts reason*: the safety floor
(security, reliability, adversarial — always-on) plus any expert whose
focus keywords appear in the claim. ``context`` can force ``domains``
(include specific experts) or ``thorough=True`` (run every expert). Order
is registry order — deterministic.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from msb_v3.moie.experts import Expert, ExpertRegistry


def select_experts(
    registry: ExpertRegistry,
    claim: str,
    context: Optional[Dict[str, Any]] = None,
) -> List[Expert]:
    context = context or {}
    lowered = claim.lower()
    forced = set(context.get("domains") or [])
    thorough = bool(context.get("thorough", False))

    chosen: List[Expert] = []
    for e in registry.list_order():
        if e.always_on:
            chosen.append(e)
        elif thorough or e.expert_id in forced:
            chosen.append(e)
        elif any(kw in lowered for kw in e.focus_keywords):
            chosen.append(e)
    return chosen
