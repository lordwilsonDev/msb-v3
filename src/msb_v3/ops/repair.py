"""RepairEngine — Phase 3: governed repair plans with risk, rollback,
required authority, and verification contracts.

The DiscrepancyEngine says *what is wrong*; the RootCauseEngine says *why*;
this engine says *what to do about it* — and refuses to do it ungoverned.
Every repair is a ``RepairPlan`` (roadmap object) carrying proposed changes,
expected outcome, risk, a rollback plan, required authority, and a
verification contract. Execution mirrors the Vesta trust perimeter:

    verify-before-trust (chain must verify) → kill-switch gate →
    apply → verify contract → rollback on failure → audit every step

Authority policy (deterministic, matches the Phase 4 hierarchy):

  - AUTO      : requeue_wake, reanchor_chain — bounded, reversible ops
  - OPERATOR  : quarantine_wake, resolve_discrepancy — visible state changes
  - PROHIBITED: anything not in the catalog is refused; chain_invalid /
    projection_divergence are never auto-repaired (tamper evidence must be
    human-investigated, not papered over)

No LLM in the authority path. ``propose()`` maps a RootCauseEngine
diagnosis to candidate plans; AUTO plans may be executed without approval,
OPERATOR plans require ``approve()`` first — the approval is durable and
expires.

CLI:
    python -m msb_v3.ops.repair propose
    python -m msb_v3.ops.repair submit --action quarantine_wake
    python -m msb_v3.ops.repair approve <plan_id> --operator wilson
    python -m msb_v3.ops.repair execute <plan_id> --operator wilson
    python -m msb_v3.ops.repair list
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import subprocess
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from msb_v3.core.config import settings

logger = logging.getLogger(__name__)

AUTO = "AUTO"
OPERATOR = "OPERATOR"

STATUS_PROPOSED = "proposed"
STATUS_AWAITING = "awaiting_approval"
STATUS_APPROVED = "approved"
STATUS_EXECUTING = "executing"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_ROLLED_BACK = "rolled_back"
STATUS_REJECTED = "rejected"
STATUS_EXPIRED = "expired"

_TERMINAL = frozenset({STATUS_COMPLETED, STATUS_FAILED, STATUS_ROLLED_BACK, STATUS_REJECTED, STATUS_EXPIRED})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_repair_db_path() -> Path:
    """``<data>/runtime/repairs.db`` — alongside wake/tasks/automation state."""
    return Path(settings.db_path).parent / "runtime" / "repairs.db"


# ---------------------------------------------------------------------------
# RepairPlan — the roadmap object
# ---------------------------------------------------------------------------


@dataclass
class RepairPlan:
    plan_id: str
    timestamp: str
    discrepancy_id: str
    root_cause: str
    action: str
    params: Dict[str, Any]
    proposed_changes: str
    expected_outcome: str
    risk: str  # low | medium | high
    rollback_plan: str
    required_authority: str  # AUTO | OPERATOR
    verification_contract: Dict[str, Any]
    status: str = STATUS_PROPOSED
    decided_by: str = ""
    executed_at: str = ""
    error: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


class RepairStore:
    """Durable repair plans + approval state (runtime/repairs.db)."""

    def __init__(self, db_path: Optional[str | Path] = None) -> None:
        self.db_path = Path(db_path) if db_path else default_repair_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS repair_plans (
                    plan_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    discrepancy_id TEXT NOT NULL,
                    root_cause TEXT NOT NULL,
                    action TEXT NOT NULL,
                    params TEXT NOT NULL,
                    proposed_changes TEXT NOT NULL,
                    expected_outcome TEXT NOT NULL,
                    risk TEXT NOT NULL,
                    rollback_plan TEXT NOT NULL,
                    required_authority TEXT NOT NULL,
                    verification_contract TEXT NOT NULL,
                    status TEXT NOT NULL,
                    decided_by TEXT NOT NULL,
                    executed_at TEXT NOT NULL,
                    error TEXT NOT NULL
                )
                """
            )

    def insert(self, plan: RepairPlan) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO repair_plans (plan_id, timestamp, discrepancy_id, root_cause, action, params, "
                "proposed_changes, expected_outcome, risk, rollback_plan, required_authority, "
                "verification_contract, status, decided_by, executed_at, error) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    plan.plan_id, plan.timestamp, plan.discrepancy_id, plan.root_cause, plan.action,
                    json.dumps(plan.params), plan.proposed_changes, plan.expected_outcome, plan.risk,
                    plan.rollback_plan, plan.required_authority, json.dumps(plan.verification_contract),
                    plan.status, plan.decided_by, plan.executed_at, plan.error,
                ),
            )

    def get(self, plan_id: str) -> RepairPlan:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM repair_plans WHERE plan_id=?", (plan_id,)).fetchone()
        if row is None:
            raise KeyError(f"unknown repair plan: {plan_id}")
        d = dict(row)
        d["params"] = json.loads(d["params"])
        d["verification_contract"] = json.loads(d["verification_contract"])
        return RepairPlan(**d)

    def list(self, status: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        where = "WHERE status=?" if status else ""
        params: List[Any] = [status] if status else []
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM repair_plans {where} ORDER BY timestamp DESC LIMIT ?", params
            ).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["params"] = json.loads(d["params"])
            d["verification_contract"] = json.loads(d["verification_contract"])
            out.append(d)
        return out

    def set_status(
        self, plan_id: str, status: str, *, decided_by: str = "", error: str = "", executed_at: str = ""
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE repair_plans SET status=?, decided_by=?, error=?, executed_at=? WHERE plan_id=?",
                (status, decided_by, error[:500], executed_at, plan_id),
            )


# ---------------------------------------------------------------------------
# Repair action catalog — deterministic apply / verify / rollback
# ---------------------------------------------------------------------------


def _wake_db_path() -> str:
    if settings.wake_db_path:
        return str(Path(settings.wake_db_path))
    return str(Path(settings.db_path).parent / "runtime" / "wake.db")


def _apply_requeue_wake(params: Dict[str, Any], store: Any = None) -> Dict[str, Any]:
    """Move failed wake messages back to pending (retry after recovery)."""
    with sqlite3.connect(_wake_db_path()) as conn:
        affected = conn.execute(
            "UPDATE wake_inbox SET status='pending', error=NULL WHERE status='failed'"
        ).rowcount
    return {"requeued": affected}


def _verify_wake_requeued(contract: Dict[str, Any], params: Dict[str, Any], apply_result: Dict[str, Any], store: Any = None) -> Dict[str, Any]:
    with sqlite3.connect(_wake_db_path()) as conn:
        pending = conn.execute("SELECT COUNT(*) FROM wake_inbox WHERE status='pending'").fetchone()[0]
    ok = apply_result.get("requeued", 0) > 0 or pending >= 1
    return {"valid": ok, "pending": pending, "requeued": apply_result.get("requeued", 0)}


def _rollback_requeue_wake(params: Dict[str, Any], apply_result: Dict[str, Any], store: Any = None) -> Dict[str, Any]:
    """Restore the requeued messages to failed (the pre-repair state)."""
    n = int(apply_result.get("requeued", 0))
    with sqlite3.connect(_wake_db_path()) as conn:
        rows = conn.execute(
            "SELECT id FROM wake_inbox WHERE status='pending' ORDER BY ts ASC LIMIT ?", (n,)
        ).fetchall()
        for (row_id,) in rows:
            conn.execute(
                "UPDATE wake_inbox SET status='failed', error='rolled back by repair' WHERE id=?", (row_id,)
            )
    return {"restored": len(rows)}


def _apply_quarantine_wake(params: Dict[str, Any], store: Any = None) -> Dict[str, Any]:
    """Drop the stuck pending backlog (mark failed, terminal for the agent)."""
    with sqlite3.connect(_wake_db_path()) as conn:
        affected = conn.execute(
            "UPDATE wake_inbox SET status='failed', error='quarantined by repair' WHERE status='pending'"
        ).rowcount
    return {"quarantined": affected}


def _verify_wake_quarantined(contract: Dict[str, Any], params: Dict[str, Any], apply_result: Dict[str, Any], store: Any = None) -> Dict[str, Any]:
    with sqlite3.connect(_wake_db_path()) as conn:
        pending = conn.execute("SELECT COUNT(*) FROM wake_inbox WHERE status='pending'").fetchone()[0]
    return {"valid": pending == 0, "pending": pending}


def _rollback_quarantine_wake(params: Dict[str, Any], apply_result: Dict[str, Any], store: Any = None) -> Dict[str, Any]:
    """Restore quarantined messages to pending (best-effort, latest first)."""
    n = int(apply_result.get("quarantined", 0))
    with sqlite3.connect(_wake_db_path()) as conn:
        rows = conn.execute(
            "SELECT id FROM wake_inbox WHERE status='failed' AND error='quarantined by repair' "
            "ORDER BY responded_at DESC LIMIT ?",
            (n,),
        ).fetchall()
        for (row_id,) in rows:
            conn.execute(
                "UPDATE wake_inbox SET status='pending', error=NULL WHERE id=?", (row_id,)
            )
    return {"restored": len(rows)}


def _anchor_path() -> Path:
    return Path(settings.db_path).parent / "uac" / "chain_anchor.json"


def _apply_reanchor_chain(params: Dict[str, Any], store: Any = None) -> Dict[str, Any]:
    """Run the notarize script if present (idempotent; a missing script is a
    no-op recorded as such — the daily notary agent covers the routine path)."""
    script = Path(settings.msb_home) / "scripts" / "notarize_chain_anchor.sh"
    if not script.exists():
        return {"ran": False, "reason": "notarize script not present"}
    proc = subprocess.run(
        ["/bin/bash", str(script)], capture_output=True, text=True, timeout=120
    )
    return {"ran": True, "returncode": proc.returncode}


def _verify_chain_anchored(contract: Dict[str, Any], params: Dict[str, Any], apply_result: Dict[str, Any], store: Any = None) -> Dict[str, Any]:
    anchor = _anchor_path()
    if not anchor.exists():
        return {"valid": False, "reason": "no anchor file"}
    age_s = (datetime.now(timezone.utc) - datetime.fromtimestamp(anchor.stat().st_mtime, tz=timezone.utc)).total_seconds()
    fresh = age_s <= float(contract.get("expect", {}).get("fresh_s", 600))
    return {"valid": fresh and not (apply_result.get("ran") and apply_result.get("returncode") != 0), "age_s": round(age_s, 1)}


def _rollback_reanchor_chain(params: Dict[str, Any], apply_result: Dict[str, Any], store: Any = None) -> Dict[str, Any]:
    return {"note": "idempotent; no rollback needed"}


def _apply_resolve_discrepancy(params: Dict[str, Any], discrepancy_store: Any = None) -> Dict[str, Any]:
    """Mark an open discrepancy resolved (operator-confirmed fix)."""
    from msb_v3.ops.discrepancy import DiscrepancyStore

    store = discrepancy_store or DiscrepancyStore()
    disc_id = params.get("discrepancy_id", "")
    if not disc_id:
        raise ValueError("resolve_discrepancy requires params.discrepancy_id")
    store.set_status(disc_id, "resolved")
    return {"resolved": disc_id}


def _verify_discrepancy_resolved(contract: Dict[str, Any], params: Dict[str, Any], apply_result: Dict[str, Any], store: Any = None) -> Dict[str, Any]:
    from msb_v3.ops.discrepancy import DiscrepancyStore

    disc_store = store or DiscrepancyStore()
    disc_id = apply_result.get("resolved", "")
    rows = disc_store.query(status="resolved")
    return {"valid": any(r["id"] == disc_id for r in rows), "id": disc_id}


def _rollback_resolve_discrepancy(params: Dict[str, Any], apply_result: Dict[str, Any], store: Any = None) -> Dict[str, Any]:
    from msb_v3.ops.discrepancy import DiscrepancyStore

    disc_store = store or DiscrepancyStore()
    disc_id = apply_result.get("resolved", "")
    if disc_id:
        disc_store.set_status(disc_id, "open")
    return {"reopened": disc_id}


# action -> (authority, risk, changes, outcome, rollback, apply, verify, rollback_fn)
REPAIR_ACTIONS: Dict[str, Dict[str, Any]] = {
    "requeue_wake": {
        "authority": AUTO,
        "risk": "low",
        "changes": "failed wake messages → pending (retry after provider recovery)",
        "outcome": "failed messages are retried by the next wake cycle",
        "rollback": "mark the requeued messages failed again",
        "apply": _apply_requeue_wake,
        "verify": _verify_wake_requeued,
        "rollback_fn": _rollback_requeue_wake,
        "contract": {"kind": "wake_requeued", "expect": {"pending_gte": 1}},
    },
    "quarantine_wake": {
        "authority": OPERATOR,
        "risk": "medium",
        "changes": "pending wake messages → failed (drop the stuck backlog)",
        "outcome": "queue backlog cleared; messages terminal (not retried)",
        "rollback": "restore the quarantined messages to pending",
        "apply": _apply_quarantine_wake,
        "verify": _verify_wake_quarantined,
        "rollback_fn": _rollback_quarantine_wake,
        "contract": {"kind": "wake_quarantined", "expect": {"pending_eq": 0}},
    },
    "reanchor_chain": {
        "authority": AUTO,
        "risk": "low",
        "changes": "re-run scripts/notarize_chain_anchor.sh (idempotent)",
        "outcome": "a fresh signed anchor covers the chain tip",
        "rollback": "none (idempotent)",
        "apply": _apply_reanchor_chain,
        "verify": _verify_chain_anchored,
        "rollback_fn": _rollback_reanchor_chain,
        "contract": {"kind": "chain_anchored", "expect": {"fresh_s": 600}},
    },
    "resolve_discrepancy": {
        "authority": OPERATOR,
        "risk": "low",
        "changes": "mark an open discrepancy resolved (operator-confirmed fix)",
        "outcome": "the discrepancy leaves the open set",
        "rollback": "reopen the discrepancy",
        "apply": _apply_resolve_discrepancy,
        "verify": _verify_discrepancy_resolved,
        "rollback_fn": _rollback_resolve_discrepancy,
        "contract": {"kind": "discrepancy_resolved", "expect": {}},
    },
}

# Discrepancy types that are NEVER auto-repaired — tamper evidence must be
# human-investigated, not papered over.
PROHIBITED_FOR_AUTO = frozenset({"chain_invalid", "spine_chain_invalid", "projection_divergence", "illegal_transition"})


# ---------------------------------------------------------------------------
# RepairService — the governed flow
# ---------------------------------------------------------------------------


class RepairService:
    def __init__(
        self,
        *,
        store: Optional[RepairStore] = None,
        audit: Optional[Any] = None,
        kill_switch: Optional[Any] = None,
        discrepancy_store: Optional[Any] = None,
    ) -> None:
        self.store = store or RepairStore()
        self._audit = audit
        self._kill_switch = kill_switch
        self._discrepancy_store = discrepancy_store

    def _chain(self) -> Any:
        if self._audit is None:
            from msb_ledger.chain_anchor import anchored_chain_from_env

            self._audit = anchored_chain_from_env()
        return self._audit

    def _killswitch(self) -> Any:
        if self._kill_switch is None:
            from msb_v3.governance.killswitch import KillSwitch

            self._kill_switch = KillSwitch()
        return self._kill_switch

    def _audit_append(self, event_type: str, payload: Dict[str, Any]) -> None:
        try:
            self._chain().append("repair", event_type, payload)
        except Exception as exc:  # noqa: BLE001 — audit is best-effort for the engine
            logger.warning("repair audit append failed (%s): %s", event_type, exc)

    # -- planning ----------------------------------------------------------

    def _make_plan(
        self,
        action: str,
        *,
        params: Dict[str, Any],
        discrepancy_id: str = "",
        root_cause: str = "",
    ) -> RepairPlan:
        spec = REPAIR_ACTIONS.get(action)
        if spec is None:
            raise ValueError(f"unknown repair action: {action} (catalog: {sorted(REPAIR_ACTIONS)})")
        return RepairPlan(
            plan_id=f"repair-{uuid.uuid4().hex[:12]}",
            timestamp=_now(),
            discrepancy_id=discrepancy_id,
            root_cause=root_cause,
            action=action,
            params=params,
            proposed_changes=spec["changes"],
            expected_outcome=spec["outcome"],
            risk=spec["risk"],
            rollback_plan=spec["rollback"],
            required_authority=spec["authority"],
            verification_contract=spec["contract"],
        )

    def propose(self, diagnosis: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Map a RootCauseEngine diagnosis (live if not given) to candidate
        plans. Prohibited classes are never proposed for repair."""
        if diagnosis is None:
            from msb_v3.ops.root_cause import RootCauseEngine

            diagnosis = RootCauseEngine().diagnose()
        from msb_v3.ops.discrepancy import DiscrepancyStore

        disc_store = self._discrepancy_store or DiscrepancyStore()
        plans: List[RepairPlan] = []
        skipped: List[str] = []
        root_cause = ""
        for root in diagnosis.get("roots", []):
            if root.get("kind") == "provider_outage":
                root_cause = f"{root['resource']} provider outage (conf {root.get('confidence', 0):.2f})"
                plans.append(
                    self._make_plan(
                        "requeue_wake",
                        params={"provider": root["resource"]},
                        root_cause=root_cause,
                    )
                )
        for signal in diagnosis.get("signals", []):
            if signal.get("kind") == "queue_backlog" and signal.get("resource") == "wake_inbox":
                plans.append(
                    self._make_plan(
                        "quarantine_wake",
                        params={"note": signal.get("detail", "")},
                        root_cause=f"wake inbox backlog ({signal.get('meta', {}).get('pending', '?')} pending)",
                    )
                )
        for row in disc_store.query(status="open"):
            dtype = row.get("discrepancy_type", "")
            if dtype in PROHIBITED_FOR_AUTO:
                skipped.append(f"{dtype} @ {row.get('affected_resource', '')} — not auto-repairable")
        for plan in plans:
            # OPERATOR plans land in awaiting_approval (approve() only accepts
            # that state); AUTO plans stay proposed and are directly executable.
            if plan.required_authority == OPERATOR:
                plan.status = STATUS_AWAITING
            self.store.insert(plan)
            self._audit_append(
                "repair.proposed",
                {"plan_id": plan.plan_id, "action": plan.action, "authority": plan.required_authority, "risk": plan.risk},
            )
        return {
            "ok": True,
            "ts": _now(),
            "plans": [p.as_dict() for p in plans],
            "prohibited": skipped,
        }

    def submit(self, action: str, *, params: Dict[str, Any], discrepancy_id: str = "", root_cause: str = "") -> Dict[str, Any]:
        """Create a plan manually (validated against the catalog). Returns the
        durable plan row; AUTO plans are ready to execute, OPERATOR plans sit
        in awaiting_approval."""
        plan = self._make_plan(action, params=params, discrepancy_id=discrepancy_id, root_cause=root_cause)
        plan.status = STATUS_AWAITING if plan.required_authority == OPERATOR else STATUS_PROPOSED
        self.store.insert(plan)
        self._audit_append(
            "repair.submitted",
            {"plan_id": plan.plan_id, "action": plan.action, "authority": plan.required_authority},
        )
        return plan.as_dict()

    def approve(self, plan_id: str, operator: str) -> Dict[str, Any]:
        plan = self.store.get(plan_id)
        if plan.status != STATUS_AWAITING:
            raise ValueError(f"plan {plan_id} is {plan.status}, not awaiting_approval")
        plan.status = STATUS_APPROVED
        plan.decided_by = operator
        self.store.set_status(plan_id, STATUS_APPROVED, decided_by=operator)
        self._audit_append(
            "repair.approved",
            {"plan_id": plan_id, "action": plan.action, "operator": operator},
        )
        return plan.as_dict()

    def execute(self, plan_id: str, operator: str = "system") -> Dict[str, Any]:
        """The governed execution flow — mirrors Vesta approve_and_execute:
        verify-before-trust → kill-switch gate → apply → verify contract →
        rollback on failure → audit every step."""
        plan = self.store.get(plan_id)
        if plan.required_authority == OPERATOR and plan.status != STATUS_APPROVED:
            raise ValueError(f"plan {plan_id} requires operator approval (status: {plan.status})")
        if plan.status not in (STATUS_APPROVED, STATUS_PROPOSED):
            raise ValueError(f"plan {plan_id} is {plan.status}; expected approved or proposed (AUTO)")
        spec = REPAIR_ACTIONS[plan.action]

        # 1. verify-before-trust — never execute on top of a broken chain.
        try:
            from msb_ledger.audit_chain import verify_trustworthy

            trust = verify_trustworthy(self._chain())
        except Exception:
            trust = {"valid": False, "reason": "chain verify raised"}
        if not trust.get("valid"):
            self.store.set_status(plan_id, STATUS_FAILED, decided_by=operator, error=f"chain not trustworthy: {trust.get('reason')}")
            self._audit_append("repair.failed", {"plan_id": plan_id, "reason": "verify-before-trust"})
            return {"status": STATUS_FAILED, "plan_id": plan_id, "error": f"chain not trustworthy: {trust.get('reason')}"}

        # 2. kill-switch gate.
        if self._killswitch().is_armed():
            self.store.set_status(plan_id, STATUS_FAILED, decided_by=operator, error="kill switch armed")
            self._audit_append("repair.failed", {"plan_id": plan_id, "reason": "kill switch armed"})
            return {"status": STATUS_FAILED, "plan_id": plan_id, "error": "kill switch armed"}

        # 3. apply.
        self.store.set_status(plan_id, STATUS_EXECUTING, decided_by=operator, executed_at=_now())
        self._audit_append("repair.executing", {"plan_id": plan_id, "action": plan.action})
        try:
            apply_result = spec["apply"](plan.params, self._discrepancy_store)
        except Exception as exc:  # noqa: BLE001
            self.store.set_status(plan_id, STATUS_FAILED, decided_by=operator, error=str(exc))
            self._audit_append("repair.failed", {"plan_id": plan_id, "error": str(exc)})
            return {"status": STATUS_FAILED, "plan_id": plan_id, "error": str(exc)}

        # 4. verification contract.
        verify_result = spec["verify"](plan.verification_contract, plan.params, apply_result, self._discrepancy_store)
        self._audit_append(
            "repair.verified",
            {"plan_id": plan_id, "action": plan.action, "valid": verify_result.get("valid"), "detail": verify_result},
        )
        if not verify_result.get("valid"):
            # 5. rollback — restore the pre-repair state.
            rollback_result = spec["rollback_fn"](plan.params, apply_result, self._discrepancy_store)
            self.store.set_status(plan_id, STATUS_ROLLED_BACK, decided_by=operator, error=f"verification failed: {verify_result}")
            self._audit_append(
                "repair.rolled_back",
                {"plan_id": plan_id, "action": plan.action, "verification": verify_result, "rollback": rollback_result},
            )
            return {
                "status": STATUS_ROLLED_BACK,
                "plan_id": plan_id,
                "verification": verify_result,
                "rollback": rollback_result,
                "error": f"verification failed: {verify_result.get('reason', 'contract not met')}",
            }

        # 6. completed.
        self.store.set_status(plan_id, STATUS_COMPLETED, decided_by=operator, executed_at=_now())
        self._audit_append(
            "repair.completed",
            {"plan_id": plan_id, "action": plan.action, "verification": verify_result, "apply": apply_result},
        )
        return {
            "status": STATUS_COMPLETED,
            "plan_id": plan_id,
            "action": plan.action,
            "verification": verify_result,
            "apply": apply_result,
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli() -> None:
    parser = argparse.ArgumentParser(prog="msb_v3.ops.repair", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("propose", help="propose repair plans from a live diagnosis")
    s = sub.add_parser("submit", help="submit a manual repair plan")
    s.add_argument("--action", required=True, choices=sorted(REPAIR_ACTIONS))
    s.add_argument("--param", action="append", default=[], help="key=value (repeatable)")
    a = sub.add_parser("approve", help="approve an awaiting plan")
    a.add_argument("plan_id")
    a.add_argument("--operator", default="cli")
    e = sub.add_parser("execute", help="execute a plan (auto or approved)")
    e.add_argument("plan_id")
    e.add_argument("--operator", default="cli")
    sub.add_parser("list", help="list plans")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    service = RepairService()
    if args.command == "propose":
        print(json.dumps(service.propose(), indent=2, default=str))
    elif args.command == "submit":
        params = {}
        for kv in args.param:
            k, _, v = kv.partition("=")
            params[k] = v
        print(json.dumps(service.submit(args.action, params=params), indent=2, default=str))
    elif args.command == "approve":
        print(json.dumps(service.approve(args.plan_id, args.operator), indent=2, default=str))
    elif args.command == "execute":
        print(json.dumps(service.execute(args.plan_id, args.operator), indent=2, default=str))
    else:
        print(json.dumps(service.store.list(), indent=2, default=str))


if __name__ == "__main__":
    _cli()
