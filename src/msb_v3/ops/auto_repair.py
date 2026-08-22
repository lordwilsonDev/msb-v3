"""AutoRepairLoop — Phase 4: bounded automatic repair.

The Level 3→4 loop, closed. Every cycle (launchd agent, 10 minutes) the loop
runs the full governed pipeline without human touch:

    scan (DiscrepancyEngine) → diagnose (RootCauseEngine) → propose
    (RepairService) → execute AUTO plans → audit the cycle

Boundedness is the design point, not the automation:

  - AUTO authority only. ``requeue_wake`` / ``reanchor_chain`` execute
    without approval (reversible, low-risk, Level A per the roadmap).
    OPERATOR plans (``quarantine_wake``, ``resolve_discrepancy``) are
    proposed and left in awaiting_approval — the loop never approves.
  - Dedupe. One open plan per action+params (``RepairStore.has_open_plan``)
    — a provider outage yields one requeue plan, never a plan storm.
  - Per-cycle cap. ``max_auto_execute`` (default MSB_AUTO_REPAIR_MAX_EXECUTE)
    bounds executions even when many plans are open; deferred plans retry on
    later cycles.
  - Recovery guard. ``requeue_wake`` executes only after the provider has
    been quiet for ``requeue_quiet_s`` (default 15 min) — no churn while the
    outage is still live; the failed messages stay failed until the provider
    has actually recovered.
  - Kill switch. Armed → the loop does nothing but record the cycle.
  - Verify-before-trust. Every execution re-verifies the audit chain; a
    tampered chain blocks execution (and skips reanchor proposals — tamper
    evidence is never papered over).
  - No-op when healthy. No signals → no proposals → the cycle is a
    one-line audit record.

Every cycle appends an ``auto_repair.cycle`` record to the anchored audit
chain and persists a summary to ``runtime/repairs.db`` (auto_repair_cycles),
so autonomous behavior is itself auditable evidence.

CLI:
    python -m msb_v3.ops.auto_repair run                # one cycle (real)
    python -m msb_v3.ops.auto_repair run --dry-run      # simulate, change nothing
    python -m msb_v3.ops.auto_repair status             # last cycle + open plans
"""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from msb_v3.core.config import settings

logger = logging.getLogger(__name__)

AUTO = "AUTO"
OPERATOR = "OPERATOR"

STATUS_PROPOSED = "proposed"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_ROLLED_BACK = "rolled_back"

# Terminal plan states — a plan in any other state is still open.
_OPEN_STATUSES = (STATUS_PROPOSED, "awaiting_approval", "approved", "executing")

# Anchor freshness contract (mirrors the reanchor_chain action's contract).
_ANCHOR_FRESH_S = 600.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _wake_db() -> str:
    if settings.wake_db_path:
        return str(Path(settings.wake_db_path))
    return str(Path(settings.db_path).parent / "runtime" / "wake.db")


def _anchor_path() -> Path:
    return Path(settings.db_path).parent / "uac" / "chain_anchor.json"


# ---------------------------------------------------------------------------
# Cycle store — summaries of every autonomous cycle (runtime/repairs.db)
# ---------------------------------------------------------------------------


class AutoRepairStore:
    """Durable cycle summaries — same DB file as the repair plans, own table."""

    def __init__(self, db_path: Optional[str | Path] = None) -> None:
        from msb_v3.ops.repair import default_repair_db_path

        self.db_path = Path(db_path) if db_path else default_repair_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS auto_repair_cycles (
                    cycle_id TEXT PRIMARY KEY,
                    ts TEXT NOT NULL,
                    status TEXT NOT NULL,
                    report TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def insert_cycle(self, cycle_id: str, ts: str, status: str, report: Dict[str, Any]) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO auto_repair_cycles (cycle_id, ts, status, report) VALUES (?,?,?,?)",
                (cycle_id, ts, status, json.dumps(report, default=str)),
            )

    def last_cycle(self) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM auto_repair_cycles ORDER BY ts DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["report"] = json.loads(d["report"])
        return d

    def recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM auto_repair_cycles ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["report"] = json.loads(d["report"])
            out.append(d)
        return out


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


class AutoRepairLoop:
    """One autonomous cycle: scan → diagnose → propose → execute AUTO → audit."""

    def __init__(
        self,
        *,
        service: Optional[Any] = None,
        store: Optional[AutoRepairStore] = None,
        discrepancy_engine: Optional[Any] = None,
        root_cause_engine: Optional[Any] = None,
        discrepancy_store: Optional[Any] = None,
        chain: Optional[Any] = None,
        kill_switch: Optional[Any] = None,
    ) -> None:
        from msb_v3.ops.repair import RepairService

        self._service = service or RepairService()
        self.store = store or AutoRepairStore()
        self._disc_engine = discrepancy_engine
        self._root_cause_engine = root_cause_engine
        self._discrepancy_store = discrepancy_store
        self._chain = chain
        self._kill_switch = kill_switch
        self._lock_fh: Any = None

    # -- dependency resolution (lazy, like RepairService) -------------------

    def _disc(self) -> Any:
        if self._disc_engine is None:
            from msb_v3.ops.discrepancy import DiscrepancyEngine

            self._disc_engine = DiscrepancyEngine()
        return self._disc_engine

    def _rc(self) -> Any:
        if self._root_cause_engine is None:
            from msb_v3.ops.root_cause import RootCauseEngine

            self._root_cause_engine = RootCauseEngine()
        return self._root_cause_engine

    def _disc_store(self) -> Any:
        if self._discrepancy_store is None:
            from msb_v3.ops.discrepancy import DiscrepancyStore

            self._discrepancy_store = DiscrepancyStore()
        return self._discrepancy_store

    def _chain_obj(self) -> Any:
        if self._chain is None:
            from msb_ledger.chain_anchor import anchored_chain_from_env

            self._chain = anchored_chain_from_env()
        return self._chain

    def _killswitch(self) -> Any:
        if self._kill_switch is None:
            from msb_v3.governance.killswitch import KillSwitch

            self._kill_switch = KillSwitch()
        return self._kill_switch

    def _audit_append(self, event_type: str, payload: Dict[str, Any]) -> None:
        try:
            self._chain_obj().append("auto_repair", event_type, payload)
        except Exception as exc:  # noqa: BLE001 — audit is best-effort for the loop
            logger.warning("auto_repair audit append failed (%s): %s", event_type, exc)

    # -- concurrency --------------------------------------------------------

    def _acquire_lock(self) -> bool:
        """One cycle at a time (flock on runtime/auto_repair.lock). A run that
        finds the lock held reports already_running instead of stepping on an
        in-flight cycle."""
        lock_path = Path(settings.db_path).parent / "runtime" / "auto_repair.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._lock_fh = open(lock_path, "w")
            fcntl.flock(self._lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    # -- guards -------------------------------------------------------------

    @staticmethod
    def _chain_trust(chain: Any) -> tuple:
        try:
            from msb_ledger.audit_chain import verify_trustworthy

            trust = verify_trustworthy(chain)
        except Exception as exc:  # noqa: BLE001
            return False, f"verify raised {exc.__class__.__name__}: {exc}"
        return bool(trust.get("valid")), str(trust.get("reason", ""))

    def _anchor_age_s(self) -> float:
        anchor = _anchor_path()
        if not anchor.exists():
            return float("inf")
        return (
            datetime.now(timezone.utc)
            - datetime.fromtimestamp(anchor.stat().st_mtime, tz=timezone.utc)
        ).total_seconds()

    def _provider_quiet(self, provider: str, quiet_s: float) -> bool:
        """No failed wake messages naming the provider within the last
        ``quiet_s`` — the recovery evidence the requeue guard requires."""
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=quiet_s)).isoformat()
        try:
            with sqlite3.connect(_wake_db()) as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM wake_inbox WHERE status='failed' AND ts>=? AND error LIKE ?",
                    (cutoff, f"%{provider}%"),
                ).fetchone()
        except sqlite3.Error:
            return False  # unreadable store → not quiet → no requeue (fail-closed)
        return int(row[0]) == 0

    def _execution_guard(self, plan: Dict[str, Any], requeue_quiet_s: float) -> tuple:
        """(ready, reason) — the boundedness checks before executing one plan."""
        if plan["action"] == "requeue_wake":
            provider = plan.get("params", {}).get("provider", "")
            if not provider:
                return False, "requeue_wake without a provider"
            if not self._provider_quiet(provider, requeue_quiet_s):
                return False, f"provider {provider} still failing (fresh failures in window)"
            return True, ""
        # reanchor_chain: idempotent; chain validity is re-checked inside
        # RepairService.execute (verify-before-trust) regardless.
        return True, ""

    # -- the cycle ----------------------------------------------------------

    def run(
        self,
        *,
        dry_run: bool = False,
        max_auto_execute: Optional[int] = None,
        requeue_quiet_s: float = 900.0,
    ) -> Dict[str, Any]:
        cycle_id = f"ar-{uuid.uuid4().hex[:12]}"
        ts = _now()
        max_exec = int(max_auto_execute if max_auto_execute is not None else settings.auto_repair_max_execute)
        if max_exec < 0:
            max_exec = 0

        # 0. disabled / locked / kill-switched — the loop may act only when
        #    explicitly enabled, alone, and the governance brakes disarmed.
        if not settings.auto_repair_enabled:
            return self._finish(cycle_id, ts, "disabled", {"reason": "MSB_AUTO_REPAIR_ENABLED=0"})
        if not self._acquire_lock():
            return self._finish(cycle_id, ts, "already_running", {})
        if self._killswitch().is_armed():
            return self._finish(cycle_id, ts, "kill_switch_armed", {})

        # 1. the evidence: scan + diagnose + chain trust (all read-only).
        try:
            scan = self._disc().scan()
        except Exception as exc:  # noqa: BLE001 — a broken scan is a finding, not a crash
            scan = {"ok": False, "error": f"{exc.__class__.__name__}: {exc}"}
        try:
            diagnosis = self._rc().diagnose()
        except Exception as exc:  # noqa: BLE001
            return self._finish(cycle_id, ts, "error", {"error": f"diagnose failed: {exc}"})
        chain_valid, chain_reason = self._chain_trust(self._chain_obj())
        anchor_stale = self._anchor_age_s() > _ANCHOR_FRESH_S

        # 2. what WOULD be proposed (pure mapping — shared with propose()).
        from msb_v3.ops.repair import _finalize_plan, plans_for_diagnosis

        candidates, prohibited = plans_for_diagnosis(diagnosis, disc_store=self._disc_store())
        would: List[Dict[str, Any]] = []
        for p in candidates:
            would.append(_finalize_plan(p).as_dict())
        if chain_valid and anchor_stale:
            age_s = self._anchor_age_s()
            age_label = f"{round(age_s)}s" if age_s != float("inf") else "missing"
            would.append(
                {
                    "action": "reanchor_chain",
                    "params": {},
                    "root_cause": f"anchor {age_label} > {_ANCHOR_FRESH_S:.0f}s",
                    "required_authority": AUTO,
                    "risk": "low",
                }
            )

        # 3. dry-run stops here: the report is the plan, nothing changes.
        if dry_run:
            return self._finish(
                cycle_id,
                ts,
                "dry_run",
                {
                    "scan": scan,
                    "diagnosis": diagnosis,
                    "chain": {"valid": chain_valid, "reason": chain_reason},
                    "would_propose": would,
                    "prohibited": prohibited,
                },
            )

        # 4. propose, deduped — one open plan per action+params. The reanchor
        #    candidate is submitted directly (it is not diagnosis-derived);
        #    the rest go through RepairService.propose (same mapping, dedupe).
        skipped_dupes = 0
        proposed: List[Dict[str, Any]] = []
        for p in would:
            if self._service.store.has_open_plan(p["action"], p.get("params", {})):
                skipped_dupes += 1
                continue
            if p["action"] == "reanchor_chain":
                created = self._service.submit(
                    "reanchor_chain",
                    params={},
                    root_cause=p["root_cause"],
                )
                proposed.append(created)
        report = self._service.propose(diagnosis=diagnosis, dedupe=True)
        proposed.extend(report["plans"])

        # 5. execute AUTO plans (this cycle's + deferred from earlier), oldest
        #    first, capped — everything else stays gated.
        open_auto = [
            p
            for p in self._service.store.list()
            if p["required_authority"] == AUTO and p["status"] == STATUS_PROPOSED
        ]
        open_auto.sort(key=lambda p: p["timestamp"])
        executed: List[Dict[str, Any]] = []
        deferred: List[Dict[str, Any]] = []
        failed: List[Dict[str, Any]] = []
        for plan in open_auto:
            if len(executed) >= max_exec:
                deferred.append({"plan_id": plan["plan_id"], "reason": f"cap {max_exec} reached"})
                continue
            ready, reason = self._execution_guard(plan, requeue_quiet_s)
            if not ready:
                deferred.append({"plan_id": plan["plan_id"], "reason": reason})
                continue
            try:
                result = self._service.execute(plan["plan_id"], operator="auto-repair")
            except Exception as exc:  # noqa: BLE001
                failed.append({"plan_id": plan["plan_id"], "error": f"{exc.__class__.__name__}: {exc}"})
                continue
            if result.get("status") == STATUS_COMPLETED:
                executed.append({"plan_id": plan["plan_id"], "action": plan["action"], "apply": result.get("apply")})
            else:
                failed.append({"plan_id": plan["plan_id"], "status": result.get("status"), "error": result.get("error")})

        return self._finish(
            cycle_id,
            ts,
            "completed",
            {
                "scan": scan,
                "diagnosis": diagnosis,
                "chain": {"valid": chain_valid, "reason": chain_reason},
                "proposed": proposed,
                "deduped": skipped_dupes,
                "executed": executed,
                "deferred": deferred,
                "failed": failed,
                "prohibited": prohibited,
            },
        )

    def _finish(self, cycle_id: str, ts: str, status: str, report: Dict[str, Any]) -> Dict[str, Any]:
        """Persist the cycle summary and mirror it to the anchored chain."""
        summary: Dict[str, Any] = {
            "ok": True,
            "cycle_id": cycle_id,
            "ts": ts,
            "status": status,
            "enabled": settings.auto_repair_enabled,
        }
        summary.update(report)
        try:
            self.store.insert_cycle(cycle_id, ts, status, summary)
        except Exception as exc:  # noqa: BLE001
            logger.warning("auto_repair cycle persist failed: %s", exc)
        self._audit_append(
            "auto_repair.cycle",
            {
                "cycle_id": cycle_id,
                "status": status,
                "proposed": len(report.get("proposed", [])),
                "executed": len(report.get("executed", [])),
                "deferred": len(report.get("deferred", [])),
                "failed": len(report.get("failed", [])),
                "dry_run": status == "dry_run",
            },
        )
        return summary

    # -- status -------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        open_plans = [p for p in self._service.store.list() if p["status"] in _OPEN_STATUSES]
        return {
            "ok": True,
            "ts": _now(),
            "enabled": settings.auto_repair_enabled,
            "schedule": "launchd com.blackswanlabz.msb-v3.auto-repair (StartInterval 600)",
            "max_auto_execute": settings.auto_repair_max_execute,
            "last_cycle": self.store.last_cycle(),
            "open_plans": len(open_plans),
            "open_plan_actions": sorted({p["action"] for p in open_plans}),
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli() -> None:
    parser = argparse.ArgumentParser(prog="msb_v3.ops.auto_repair", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="run one autonomous repair cycle")
    r.add_argument("--dry-run", action="store_true", help="simulate only — propose nothing, execute nothing")
    r.add_argument("--max-execute", type=int, default=None, help="cap AUTO executions this cycle")
    r.add_argument("--quiet-window", type=float, default=900.0, help="provider quiet window for requeue (s)")
    sub.add_parser("status", help="last cycle + open plans")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    loop = AutoRepairLoop()
    if args.command == "run":
        result = loop.run(
            dry_run=args.dry_run,
            max_auto_execute=args.max_execute,
            requeue_quiet_s=args.quiet_window,
        )
        print(json.dumps(result, indent=2, default=str))
    else:
        print(json.dumps(loop.status(), indent=2, default=str))


if __name__ == "__main__":
    _cli()
