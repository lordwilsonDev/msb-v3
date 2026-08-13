"""Guard — the flywheel's single enforcement point.

The loop calls ``check_run()`` before every autonomous action and must
honor the verdict. Checks, in order:

1. kill switch — armed => HALT (fail-closed);
2. approval gate — irreversible kinds need an APPROVED item (see
   ApprovalQueue.APPROVAL_KINDS); missing/pending/rejected => refuse;
3. budget — the action's category units are spent only once the action
   will actually run;
4. governor — the iteration's convergence signals (novelty, duplicate
   ratio) feed Ouroboros; HALT surfaces, SLOW permits at reduced pace.

Every refusal is written to the UAC audit chain (governance.blocked) so a
stopped loop is explainable, not a black box. ``record_action`` is the
thin helper the loop uses to audit the actions it *does* execute (wired
to the real loop in Phase 2; the audit surface exists now).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from msb_v3.governance.approval import APPROVAL_KINDS, ApprovalQueue
from msb_v3.governance.budget import BudgetLedger
from msb_v3.governance.governor import GovernorVerdict, OuroborosGovernor
from msb_v3.governance.killswitch import KillSwitch
from msb_v3.uac.audit_chain import AuditChain


@dataclass
class GuardVerdict:
    allowed: bool
    action: str  # OK | SLOW | HALT | APPROVAL_REQUIRED | APPROVAL_PENDING
    reason: str
    detail: Dict[str, Any] = field(default_factory=dict)


class Guard:
    def __init__(
        self,
        killswitch: KillSwitch,
        ledger: BudgetLedger,
        queue: ApprovalQueue,
        governor: OuroborosGovernor,
        audit_chain: Optional[AuditChain] = None,
    ) -> None:
        self._switch = killswitch
        self._ledger = ledger
        self._queue = queue
        self._governor = governor
        self._audit = audit_chain if audit_chain is not None else AuditChain()

    def check_run(
        self,
        action: str,
        kind: Optional[str] = None,
        budget_units: Optional[Dict[str, int]] = None,
        approval_id: Optional[str] = None,
        signal: Optional[Dict[str, Any]] = None,
    ) -> GuardVerdict:
        """Gate one autonomous action. Caller must honor the verdict."""
        # 1. Kill switch — cheapest, most absolute, fail-closed.
        if self._switch.is_armed():
            return self._block("HALT", "kill switch armed — loop paused", {"action": action})

        # 2. Approval gate — irreversible work never runs without an
        #    APPROVED item of the matching kind.
        if kind in APPROVAL_KINDS:
            if approval_id is None:
                return self._block(
                    "APPROVAL_REQUIRED",
                    f"{kind} requires owner approval before execution",
                    {"action": action, "kind": kind},
                )
            item = self._queue.get(approval_id)
            if item is None or item.kind != kind:
                return self._block(
                    "APPROVAL_REQUIRED",
                    f"approval {approval_id!r} unknown or not for kind {kind}",
                    {"action": action, "kind": kind, "approval_id": approval_id},
                )
            if item.status == "PENDING":
                return self._block(
                    "APPROVAL_PENDING",
                    f"approval {approval_id} awaiting owner decision",
                    {"action": action, "kind": kind, "approval_id": approval_id},
                )
            if item.status != "APPROVED":
                return self._block(
                    "APPROVAL_REQUIRED",
                    f"approval {approval_id} not approved ({item.status})",
                    {"action": action, "kind": kind, "approval_id": approval_id},
                )

        # 3. Budget — consume only for work that will actually run.
        if budget_units:
            for category, units in budget_units.items():
                if not self._ledger.spend(category, units):
                    return self._block(
                        "HALT",
                        f"budget cap hit: {category}",
                        {"action": action, "category": category},
                    )

        # 4. Governor — convergence signals from this iteration.
        if signal is not None:
            verdict: GovernorVerdict = self._governor.advise(
                proposal_id=str(signal.get("proposal_id", action)),
                novelty=float(signal.get("novelty", 1.0)),
                duplicate_ratio=float(signal.get("duplicate_ratio", 0.0)),
            )
            if verdict.action == "HALT":
                return self._block("HALT", verdict.reason, verdict.metrics)
            if verdict.action == "SLOW":
                return GuardVerdict(
                    True, "SLOW", verdict.reason, verdict.metrics
                )

        return GuardVerdict(True, "OK", "all brakes clear", {})

    def _block(self, action: str, reason: str, detail: Dict[str, Any]) -> GuardVerdict:
        self._audit.append(
            "governance", "blocked",
            {"action": action, "reason": reason, "detail": detail},
        )
        return GuardVerdict(False, action, reason, detail)

    def record_action(self, component: str, event_type: str, payload: Dict[str, Any]) -> None:
        """Audit an executed autonomous action (Phase 2 loop wires this)."""
        self._audit.append(component, event_type, payload)
