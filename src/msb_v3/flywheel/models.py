"""Flywheel domain model — the Research→Build loop from blueprint §0.5.

The nine stages in the blueprint's order. The engine drives a turn through
these, gated at every step by the Phase 0B brakes (kill switch, budget,
approvals, governor).

- verify_novelty  : does it already exist? (gate: build only if not)
- draft_blueprint : write the turn's blueprint document
- charge          : AIL + MoIE research -> UIM (pluggable charger)
- update_blueprint: merge the UIM into the blueprint
- scan_papers     : do new papers solve a standing problem? (pluggable scanner)
- surface_problems: surface next problems from the UIM + scan
- build           : run the skill / build the thing  [APPROVAL]
- combine         : combine with something new       [APPROVAL]
- record          : record to the vault              [APPROVAL]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

STAGES = (
    "verify_novelty",
    "draft_blueprint",
    "charge",
    "update_blueprint",
    "scan_papers",
    "surface_problems",
    "build",
    "combine",
    "record",
)

# Blueprint §0.6's irreversible stages -> approval-queue kinds. The guard
# enforces these; the engine auto-submits the item and parks until the
# owner decides.
APPROVAL_STAGES: Dict[str, str] = {
    "build": "build",
    "combine": "combine",
    "record": "vault_write",
}

# Budget: every stage spends 1 iteration; charge + scan_papers are the
# research-call spenders.
ITERATIONS_PER_STAGE = 1
RESEARCH_STAGES = ("charge", "scan_papers")

TURN_STATUSES = (
    "PENDING", "RUNNING", "WAITING_APPROVAL", "DONE",
    "HALTED", "BLOCKED", "ALREADY_EXISTS", "ERROR",
)


@dataclass
class Turn:
    turn_id: str
    problem: str
    status: str = "PENDING"
    stage: str = STAGES[0]
    charger: str = "stub"
    skill: str = ""
    novelty: float = 1.0
    approval_ids: Dict[str, str] = field(default_factory=dict)
    blueprint_path: Optional[str] = None
    uim_path: Optional[str] = None
    build_path: Optional[str] = None
    combine_path: Optional[str] = None
    record_path: Optional[str] = None
    notes: List[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
