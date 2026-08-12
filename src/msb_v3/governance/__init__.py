"""Autonomy brakes (Phase 0B) — the Yin gates the flywheel runs behind.

Four load-bearing brakes from the adaptive-build-environment blueprint:
Ouroboros governor (convergence throttle), budget caps (fail-closed),
approval queue (restart-surviving), and kill switch (audit-logged). The
engine does not run itself until these are proven.
"""

from msb_v3.governance.approval import (
    APPROVAL_KINDS,
    ApprovalItem,
    ApprovalQueue,
    IdempotencyError,
)
from msb_v3.governance.budget import BudgetLedger
from msb_v3.governance.governor import GovernorVerdict, OuroborosGovernor
from msb_v3.governance.guard import Guard, GuardVerdict
from msb_v3.governance.killswitch import GovernanceHalt, KillSwitch

__all__ = [
    "APPROVAL_KINDS",
    "ApprovalItem",
    "ApprovalQueue",
    "BudgetLedger",
    "GovernanceHalt",
    "GovernorVerdict",
    "Guard",
    "GuardVerdict",
    "IdempotencyError",
    "KillSwitch",
    "OuroborosGovernor",
]
