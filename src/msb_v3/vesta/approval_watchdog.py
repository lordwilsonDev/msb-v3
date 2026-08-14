"""Approval-ledger watchdog — voids APPROVED approvals whose task never reached
a terminal state.

Discovered gap (MSB-GOV-EVAL-001 cascade harness, §10): when execution crashes
*before* `VestaWriteService.approve_and_execute`'s try/except (audit-engine,
persistence, or storage failures — exceptions not in the caught tuple, or
raised before the guard), the approval record is left APPROVED with the task
stuck in WAITING_APPROVAL and no terminal audit event. The mutation never
happened, but the approval ledger dangles — an APPROVED approval that can never
be re-executed safely.

This watchdog closes that loop:

  - DANGLING  : approval APPROVED + task in a non-terminal, non-recoverable
                state (RECEIVED..APPROVED, or the task row is missing) ->
                void the approval, quarantine the task, append an auditable
                `approval.voided` event (source=watchdog).
  - IN_FLIGHT : approval APPROVED + task in a recoverable state
                (EXECUTING/VERIFYING/RECOVERING) -> reported for operator
                review only; NOT auto-voided. `VestaTaskStore.recover_incomplete`
                quarantines these at restart; auto-voiding here could race a
                legitimate completion.
  - OK        : approval APPROVED + task terminal (COMPLETED/DENIED/
                QUARANTINED) -> legitimate history (a successful execution
                intentionally leaves the approval APPROVED; `void()` exists
                only for executions that never completed validly).

Void is terminal (`approvals.void` requires status=APPROVED; `approve`/`reject`
refuse non-PENDING rows), so a voided approval can never be re-decided into an
execution.

The void + quarantine + audit are three writes across three stores — there is
no cross-store transaction, so each step is best-effort with per-item error
capture into the report. Errors are surfaced, never silently skipped.

CLI:
    python -m msb_v3.vesta.approval_watchdog            # scan + void dangling
    python -m msb_v3.vesta.approval_watchdog --dry-run  # scan only, no changes
    python -m msb_v3.vesta.approval_watchdog --operator name
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from typing import Any, Dict, List, Optional

from msb_v3.uac.audit_chain import AuditChain
from msb_v3.vesta.approvals import ApprovalError, VestaApprovalStore
from msb_v3.vesta.runtime import TASK_STATES, TaskLifecycleError, VestaTaskStore

# Task states from which execution cannot validly continue to completion.
TERMINAL_TASK_STATES = frozenset({"COMPLETED", "DENIED", "QUARANTINED"})
# States where execution is genuinely in flight or awaiting operator recovery —
# recover_incomplete() quarantines these at restart; the watchdog only flags them.
RECOVERABLE_TASK_STATES = frozenset({"EXECUTING", "VERIFYING", "RECOVERING"})


class ApprovalWatchdog:
    def __init__(
        self,
        approvals: Optional[VestaApprovalStore] = None,
        tasks: Optional[VestaTaskStore] = None,
        audit: Optional[AuditChain] = None,
    ) -> None:
        self.approvals = approvals or VestaApprovalStore()
        self.tasks = tasks or VestaTaskStore()
        self.audit = audit or AuditChain()

    def _approved_rows(self) -> List[Dict[str, Any]]:
        with sqlite3.connect(self.approvals.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT approval_id, task_id, target_path, status, created_at, decided_at "
                "FROM vesta_approvals WHERE status='APPROVED'"
            ).fetchall()
        return [dict(r) for r in rows]

    def _classify(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """Classify one APPROVED approval against its task's actual state."""
        entry = {
            "approval_id": row["approval_id"],
            "task_id": row["task_id"],
            "target_path": row["target_path"],
        }
        try:
            task = self.tasks.get(row["task_id"])
            entry["task_state"] = task["state"]
        except TaskLifecycleError:
            entry["task_state"] = "MISSING"
        state = entry["task_state"]
        if state in TERMINAL_TASK_STATES:
            entry["class"] = "ok"
        elif state in RECOVERABLE_TASK_STATES:
            entry["class"] = "in_flight"
        else:
            entry["class"] = "dangling"
        return entry

    def scan(self) -> Dict[str, Any]:
        """Read-only classification of every APPROVED approval."""
        classified = [self._classify(row) for row in self._approved_rows()]
        return {
            "scanned": len(classified),
            "dangling": [e for e in classified if e["class"] == "dangling"],
            "in_flight": [e for e in classified if e["class"] == "in_flight"],
            "ok": [e for e in classified if e["class"] == "ok"],
        }

    def _void_one(self, entry: Dict[str, Any], operator: str) -> Dict[str, Any]:
        """Void one dangling approval + quarantine its task + audit the void.

        Returns the per-item result with any errors surfaced (never skipped).
        """
        result = dict(entry)
        approval_id, task_id = entry["approval_id"], entry["task_id"]
        reason = "dangling approval: task never reached a terminal state (watchdog)"
        # 1. Void the approval FIRST — terminal; blocks any re-execution.
        try:
            self.approvals.void(approval_id, reason)
            result["voided"] = True
        except ApprovalError as exc:
            result["voided"] = False
            result["void_error"] = str(exc)
        # 2. Quarantine the task so the pair is terminal and operator-visible.
        if task_id and task_id != "MISSING":
            try:
                self.tasks.transition(task_id, "QUARANTINED", reason=reason)
                result["task_quarantined"] = True
            except TaskLifecycleError as exc:
                result["task_quarantined"] = False
                result["task_error"] = str(exc)
        else:
            result["task_quarantined"] = False
            result["task_error"] = "task row missing — orphan approval"
        # 3. Auditable void event. If the audit channel is down the void has
        #    still happened (approvals DB is separate) — surfaced, not hidden.
        try:
            self.audit.append(
                "vesta",
                "approval.voided",
                {
                    "approval_id": approval_id,
                    "task_id": task_id,
                    "reason": reason,
                    "source": "watchdog",
                    "operator": operator,
                },
            )
            result["audited"] = True
        except Exception as exc:  # noqa: BLE001 — report, never swallow silently
            result["audited"] = False
            result["audit_error"] = f"{type(exc).__name__}: {exc}"
        return result

    def run(self, *, operator: str = "watchdog", dry_run: bool = False) -> Dict[str, Any]:
        report = self.scan()
        report["dry_run"] = dry_run
        report["operator"] = operator
        if dry_run:
            report["would_void"] = [e["approval_id"] for e in report["dangling"]]
            return report
        report["actions"] = [self._void_one(e, operator) for e in report["dangling"]]
        report["voided"] = [a for a in report["actions"] if a.get("voided")]
        report["errors"] = [
            {k: a.get(k) for k in ("approval_id", "void_error", "task_error", "audit_error") if a.get(k)}
            for a in report["actions"]
            if a.get("void_error") or a.get("task_error") or a.get("audit_error")
        ]
        return report


def _main() -> int:
    parser = argparse.ArgumentParser(description="Vesta approval-ledger watchdog")
    parser.add_argument("--dry-run", action="store_true", help="scan only; make no changes")
    parser.add_argument("--operator", default="watchdog", help="operator recorded on void events")
    args = parser.parse_args()

    report = ApprovalWatchdog().run(operator=args.operator, dry_run=args.dry_run)
    print(json.dumps(report, indent=2))
    return 1 if (report["dangling"] and not args.dry_run and report["errors"]) else 0


if __name__ == "__main__":
    raise SystemExit(_main())
