"""Pluggable generative brain for the flywheel.

The loop mechanics and brakes are real; the research *quality* is a
pluggable interface. Two chargers:

- StubCharger — deterministic, offline, zero-cost. Produces a UIM in the
  exact shape SovereignResearchAssistant writes ({topic, slug, phase1,
  ok}), seeded from the problem hash so the same problem always yields
  the same artifact (the first turn is reproducible before the real brain
  is attached).
- SovereignCharger — opt-in: runs the real SovereignResearchAssistant
  inversion (a local-LLM call) and returns its UIM file.

The paper scanner is similarly honest: StubScanner reports
papers_scanned: 0 with an explicit "real feed wires in Phase 2b" note —
it never fabricates a scan.
"""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Any, Dict, Optional

_PREDICTION_POOL = [
    "A 6-month longitudinal measurement shows the expected effect at 2x the control baseline.",
    "A controlled replication on an independent dataset fails to reproduce the headline effect.",
    "Adoption in the field converges on a hybrid that keeps the legacy path for edge cases.",
    "The bottleneck shifts from the studied component to its upstream dependency.",
    "Cost per unit of outcome drops only after the third iteration of the approach.",
    "The naive baseline outperforms the proposed method in low-resource regimes.",
]


class StubCharger:
    """Deterministic offline UIM — same problem -> same artifact."""

    def charge(self, problem: str, slug: str) -> Dict[str, Any]:
        seed = hashlib.sha256(problem.encode()).hexdigest()
        rng = random.Random(seed)
        picks = rng.sample(_PREDICTION_POOL, 3)
        return {
            "topic": problem,
            "slug": slug,
            "phase1": {
                "assumption": f"The dominant claim for '{problem}' is that the current approach is sufficient.",
                "inversion": f"The opposite claim: '{problem}' requires a fundamentally different approach.",
                "predictions": picks,
            },
            "ok": True,
        }


class SovereignCharger:
    """Opt-in real charger: runs the SovereignResearchAssistant inversion
    (local LLM) and returns its UIM artifact."""

    def __init__(self, runtime_root: Optional[Path] = None) -> None:
        self.runtime_root = runtime_root

    def charge(self, problem: str, slug: str) -> Dict[str, Any]:
        from msb_v3.harnesses.research_assistant import SovereignResearchAssistant

        assistant = SovereignResearchAssistant(
            topic=problem, slug=slug, runtime_root=self.runtime_root
        )
        assistant.run_inversion()  # writes {slug}_UIM.json
        uim_path = assistant.runtime_root / f"{assistant.slug}_UIM.json"
        return json.loads(uim_path.read_text())


class StubScanner:
    """Honest stub: no external paper feed yet. Surfaces problem candidates
    from the UIM itself and reports 0 papers scanned — never fakes a scan."""

    def scan(self, problem: str, uim: Dict[str, Any]) -> Dict[str, Any]:
        predictions = (uim.get("phase1") or {}).get("predictions", [])
        return {
            "papers_scanned": 0,
            "matches": [],
            "candidates": predictions[:3],
            "notes": "scanner stub — real paper feed (Tavily/NotebookLM) wires in Phase 2b",
        }
