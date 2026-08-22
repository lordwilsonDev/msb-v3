"""VerifyEngine — Phase 5: closed-loop verification.

The roadmap's last governing question, asked of every executed repair:

  1. Did the repair actually fix the original discrepancy?
  2. Did the repair create another discrepancy?

The engine captures a state snapshot BEFORE execution (the caller — the
autonomous loop or the API execute endpoint — captures it immediately before
``RepairService.execute``) and compares it against a fresh snapshot AFTER,
including a re-run of the discrepancy detectors so late-appearing findings
surface. The verdict is deterministic, computed from evidence:

  verified      — the repair's target condition is resolved AND no new
                  discrepancy is open that wasn't open before
  not_verified  — the target condition is still present (the repair did not
                  take, or something re-broke it)
  regressed     — new discrepancy(ies) appeared (the repair made something
                  worse — the roadmap's second question, answered)
  inconclusive  — evidence unavailable (unreadable stores) or no before
                  snapshot (legacy/manual verify): never a false claim

Every verification is persisted (``repair_verifications`` in repairs.db,
append-only — re-verifying a plan builds its history) and mirrored to the
anchored audit chain (``repair.verification``), so verdicts are evidence.

CLI:
    python -m msb_v3.ops.verify verify <plan_id> [--no-scan]
    python -m msb_v3.ops.verify list [--plan <plan_id>]
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from msb_v3.core.config import settings

logger = logging.getLogger(__name__)

VERDICT_VERIFIED = "verified"
VERDICT_NOT_VERIFIED = "not_verified"
VERDICT_REGRESSED = "regressed"
VERDICT_INCONCLUSIVE = "inconclusive"

# Anchor freshness used by the reanchor forward check (mirrors the action
# contract in repair.py).
_ANCHOR_FRESH_S = 600.0

# Plan states that have actually reached an executed outcome.
_VERIFIABLE = ("completed", "rolled_back")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _wake_db() -> str:
    if settings.wake_db_path:
        return str(Path(settings.wake_db_path))
    return str(Path(settings.db_path).parent / "runtime" / "wake.db")


def _anchor_path() -> Path:
    return Path(settings.db_path).parent / "uac" / "chain_anchor.json"


def _anchor_age_s() -> Optional[float]:
    anchor = _anchor_path()
    if not anchor.exists():
        return None
    try:
        return (datetime.now(timezone.utc) - datetime.fromtimestamp(anchor.stat().st_mtime, tz=timezone.utc)).total_seconds()
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Verification store — append-only verdict history (runtime/repairs.db)
# ---------------------------------------------------------------------------


class VerificationStore:
    """Durable verification rows — same DB file as the plans, own table."""

    def __init__(self, db_path: Optional[str | Path] = None) -> None:
        from msb_v3.ops.repair import default_repair_db_path

        self.db_path = Path(db_path) if db_path else default_repair_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS repair_verifications (
                    id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    forward_resolved TEXT,
                    before TEXT NOT NULL,
                    after TEXT NOT NULL,
                    new_discrepancies TEXT NOT NULL,
                    detail TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_verif_plan ON repair_verifications (plan_id)"
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def insert(
        self,
        *,
        verification_id: str,
        plan_id: str,
        action: str,
        ts: str,
        verdict: str,
        forward_resolved: Optional[bool],
        before: Dict[str, Any],
        after: Dict[str, Any],
        new_discrepancies: List[str],
        detail: str,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO repair_verifications (id, plan_id, action, ts, verdict, forward_resolved, "
                "before, after, new_discrepancies, detail) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    verification_id,
                    plan_id,
                    action,
                    ts,
                    verdict,
                    "true" if forward_resolved else ("false" if forward_resolved is False else ""),
                    json.dumps(before, default=str),
                    json.dumps(after, default=str),
                    json.dumps(new_discrepancies),
                    detail,
                ),
            )

    def latest(self, plan_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM repair_verifications WHERE plan_id=? ORDER BY ts DESC LIMIT 1",
                (plan_id,),
            ).fetchone()
        return self._row(row)

    def list(self, plan_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        where = "WHERE plan_id=?" if plan_id else ""
        params: List[Any] = [plan_id] if plan_id else []
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM repair_verifications {where} ORDER BY ts DESC LIMIT ?", params
            ).fetchall()
        return [r for r in (self._row(row) for row in rows) if r is not None]

    @staticmethod
    def _row(row: Any) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        d = dict(row)
        d["before"] = json.loads(d["before"])
        d["after"] = json.loads(d["after"])
        d["new_discrepancies"] = json.loads(d["new_discrepancies"])
        fwd = d.pop("forward_resolved")
        d["forward_resolved"] = True if fwd == "true" else (False if fwd == "false" else None)
        return d


# ---------------------------------------------------------------------------
# Verdict computation — pure, deterministic, evidence-only
# ---------------------------------------------------------------------------


def _target_resolved(action: str, params: Dict[str, Any], before: Optional[Dict[str, Any]], after: Optional[Dict[str, Any]]) -> tuple:
    """(resolved: bool|None, detail: str) — None means the evidence to judge
    the target is unavailable; never guess."""
    after = after or {}
    if action == "requeue_wake":
        provider = params.get("provider", "")
        # Distinguish "store unreadable" (None) from "zero failures" (0):
        # an empty provider dict means the repair took effect, not that the
        # evidence vanished.
        after_wake = after.get("wake_failed_by_provider")
        if after_wake is None:
            return None, f"wake store unreadable — cannot judge {provider} failed messages"
        after_failed = after_wake.get(provider, 0)
        before_wake = (before or {}).get("wake_failed_by_provider")
        before_failed = before_wake.get(provider, 0) if before_wake is not None else None
        if before is None:
            return after_failed == 0, f"failed {provider} messages after: {after_failed}"
        if before_failed is None:
            return after_failed == 0, f"failed {provider} messages: before unreadable → {after_failed}"
        return after_failed == 0, f"failed {provider} messages: {before_failed} → {after_failed}"
    if action == "quarantine_wake":
        after_pending = after.get("wake_pending")
        if after_pending is None:
            return None, "wake store unreadable — cannot judge pending backlog"
        before_pending = (before or {}).get("wake_pending", 0)
        if before is None:
            return after_pending == 0, f"pending wake messages after: {after_pending}"
        return after_pending == 0, f"pending wake messages: {before_pending} → {after_pending}"
    if action == "reanchor_chain":
        age = after.get("anchor_age_s")
        if age is None:
            return None, "anchor unreadable — cannot judge freshness"
        if before is None:
            return age <= _ANCHOR_FRESH_S, f"anchor age after: {round(age)}s"
        before_age = (before or {}).get("anchor_age_s")
        before_label = f"{round(before_age)}s" if before_age is not None else "?"
        return age <= _ANCHOR_FRESH_S, f"anchor age: {before_label} → {round(age)}s"
    if action == "resolve_discrepancy":
        target = params.get("discrepancy_id", "")
        after_ids = set(after.get("open_discrepancy_ids") or [])
        if after.get("open_discrepancy_ids") is None:
            return None, "discrepancy store unreadable — cannot judge target"
        before_ids = set((before or {}).get("open_discrepancy_ids") or [])
        resolved = target not in after_ids
        if before is None:
            return resolved, f"target {target} open after: {target in after_ids}"
        return resolved, f"target {target}: open before={target in before_ids}, open after={target in after_ids}"
    return None, f"no forward check for action {action!r}"


def _new_discrepancies(before: Optional[Dict[str, Any]], after: Optional[Dict[str, Any]]) -> tuple:
    """(new: List[str], assessed: bool) — the roadmap's second question:
    did the repair create a discrepancy that wasn't open before?"""
    if before is None:
        return [], False
    before_fps = before.get("open_fingerprints")
    after_fps = (after or {}).get("open_fingerprints")
    if before_fps is None or after_fps is None:
        return [], False
    return sorted(set(after_fps) - set(before_fps)), True


def compute_verdict(
    action: str,
    params: Dict[str, Any],
    before: Optional[Dict[str, Any]],
    after: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """The deterministic verdict for one repair. ``before`` may be None for
    legacy/manual verifies — the forward check still runs, but regression
    cannot be assessed and the verdict never overclaims."""
    resolved, forward_detail = _target_resolved(action, params, before, after)
    new, assessed = _new_discrepancies(before, after)

    if resolved is None:
        verdict = VERDICT_INCONCLUSIVE
        detail = f"forward evidence unavailable: {forward_detail}"
    elif not assessed:
        verdict = VERDICT_INCONCLUSIVE
        detail = (
            f"no before snapshot — regression not assessed. "
            f"Forward: {'resolved' if resolved else 'NOT resolved'} ({forward_detail})"
        )
    elif resolved and not new:
        verdict = VERDICT_VERIFIED
        detail = f"target resolved ({forward_detail}); no new discrepancies"
    elif not resolved and not new:
        verdict = VERDICT_NOT_VERIFIED
        detail = f"target NOT resolved ({forward_detail}); no new discrepancies"
    else:
        verdict = VERDICT_REGRESSED
        detail = f"new discrepancy(ies) appeared: {', '.join(new)} (forward {'resolved' if resolved else 'not resolved'})"

    return {
        "verdict": verdict,
        "forward_resolved": resolved,
        "forward_detail": forward_detail,
        "regression_assessed": assessed,
        "new_discrepancies": new,
        "detail": detail,
    }


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class VerifyEngine:
    """Capture state snapshots and produce closed-loop verdicts."""

    def __init__(
        self,
        *,
        store: Optional[VerificationStore] = None,
        repair_service: Optional[Any] = None,
        discrepancy_engine: Optional[Any] = None,
        discrepancy_store: Optional[Any] = None,
        chain: Optional[Any] = None,
        wake_db: Optional[str] = None,
    ) -> None:
        from msb_v3.ops.repair import RepairService

        self.store = store or VerificationStore()
        self._service = repair_service or RepairService()
        self._disc_engine = discrepancy_engine
        self._discrepancy_store = discrepancy_store
        self._chain = chain
        self._wake_db = wake_db or _wake_db()

    # -- dependencies -------------------------------------------------------

    def _disc_store(self) -> Any:
        if self._discrepancy_store is None:
            from msb_v3.ops.discrepancy import DiscrepancyStore

            self._discrepancy_store = DiscrepancyStore()
        return self._discrepancy_store

    def _disc(self) -> Any:
        if self._disc_engine is None:
            from msb_v3.ops.discrepancy import DiscrepancyEngine

            self._disc_engine = DiscrepancyEngine()
        return self._disc_engine

    def _chain_obj(self) -> Any:
        if self._chain is None:
            from msb_ledger.chain_anchor import anchored_chain_from_env

            self._chain = anchored_chain_from_env()
        return self._chain

    def _audit_append(self, event_type: str, payload: Dict[str, Any]) -> None:
        try:
            self._chain_obj().append("repair", event_type, payload)
        except Exception as exc:  # noqa: BLE001 — audit is best-effort for the engine
            logger.warning("verify audit append failed (%s): %s", event_type, exc)

    # -- snapshot -----------------------------------------------------------

    def capture(self) -> Dict[str, Any]:
        """A point-in-time state snapshot — the before/after evidence. Every
        field is failure-isolated: unreadable sources yield None, never a
        crash (a missing field makes that part of the verdict inconclusive)."""
        snap: Dict[str, Any] = {"ts": _now()}

        # open discrepancies (fingerprints + ids)
        try:
            rows = self._disc_store().query(status="open")
            snap["open_fingerprints"] = sorted(
                r["subsystem"] + ":" + r["discrepancy_type"] + ":" + r["affected_resource"] for r in rows
            )
            snap["open_discrepancy_ids"] = sorted(r["id"] for r in rows)
        except Exception as exc:  # noqa: BLE001
            logger.warning("verify snapshot discrepancies failed: %s", exc)
            snap["open_fingerprints"] = None
            snap["open_discrepancy_ids"] = None

        # wake store: failed-by-provider + pending
        try:
            with sqlite3.connect(self._wake_db) as conn:
                rows = conn.execute(
                    "SELECT error FROM wake_inbox WHERE status='failed'"
                ).fetchall()
                pending = conn.execute(
                    "SELECT COUNT(*) FROM wake_inbox WHERE status='pending'"
                ).fetchone()[0]
            from msb_v3.ops.root_cause import parse_error

            by_provider: Dict[str, int] = {}
            for (error,) in rows:
                provider = parse_error(error or "").get("provider") or "unknown"
                by_provider[provider] = by_provider.get(provider, 0) + 1
            snap["wake_failed_by_provider"] = by_provider
            snap["wake_pending"] = int(pending)
        except Exception as exc:  # noqa: BLE001
            logger.warning("verify snapshot wake failed: %s", exc)
            snap["wake_failed_by_provider"] = None
            snap["wake_pending"] = None

        snap["anchor_age_s"] = _anchor_age_s()
        return snap

    # -- the verdict --------------------------------------------------------

    def verify_repair(self, plan_id: str, before: Optional[Dict[str, Any]] = None, *, scan: bool = True) -> Dict[str, Any]:
        """Closed-loop verification for one executed plan: fresh scan + after
        snapshot → deterministic verdict → persisted + audited. Refuses plans
        that never reached an executed outcome."""
        plan = self._service.store.get(plan_id)
        if plan.status not in _VERIFIABLE:
            raise ValueError(f"plan {plan_id} is {plan.status!r}; verification requires {_VERIFIABLE}")

        # A fresh detector scan surfaces anything the repair may have caused
        # (the after snapshot must include it — that is the detection).
        if scan:
            try:
                self._disc().scan()
            except Exception as exc:  # noqa: BLE001 — a broken scan is itself evidence of trouble
                logger.warning("verify scan failed: %s", exc)
        after = self.capture()

        result = compute_verdict(plan.action, plan.params, before, after)
        verification_id = f"ver-{uuid.uuid4().hex[:12]}"
        ts = _now()
        self.store.insert(
            verification_id=verification_id,
            plan_id=plan_id,
            action=plan.action,
            ts=ts,
            verdict=result["verdict"],
            forward_resolved=result["forward_resolved"],
            before=before or {},
            after=after,
            new_discrepancies=result["new_discrepancies"],
            detail=result["detail"],
        )
        self._audit_append(
            "repair.verification",
            {
                "verification_id": verification_id,
                "plan_id": plan_id,
                "action": plan.action,
                "verdict": result["verdict"],
                "forward_resolved": result["forward_resolved"],
                "new_count": len(result["new_discrepancies"]),
            },
        )
        return {
            "ok": True,
            "ts": ts,
            "verification_id": verification_id,
            "plan_id": plan_id,
            "action": plan.action,
            "verdict": result["verdict"],
            "forward_resolved": result["forward_resolved"],
            "forward_detail": result["forward_detail"],
            "regression_assessed": result["regression_assessed"],
            "new_discrepancies": result["new_discrepancies"],
            "detail": result["detail"],
            "before": before or {},
            "after": after,
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli() -> None:
    parser = argparse.ArgumentParser(prog="msb_v3.ops.verify", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    v = sub.add_parser("verify", help="run closed-loop verification for one executed plan")
    v.add_argument("plan_id")
    v.add_argument("--no-scan", action="store_true", help="skip the fresh detector scan")
    l = sub.add_parser("list", help="verification history")
    l.add_argument("--plan", default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)
    engine = VerifyEngine()
    if args.command == "verify":
        print(json.dumps(engine.verify_repair(args.plan_id, scan=not args.no_scan), indent=2, default=str))
    else:
        print(json.dumps(engine.store.list(plan_id=args.plan), indent=2, default=str))


if __name__ == "__main__":
    _cli()
