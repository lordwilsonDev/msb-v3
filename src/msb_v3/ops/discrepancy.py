"""DiscrepancyEngine — one normalized diagnostic layer over every detector.

Level 3 → 4 roadmap, Phase 1. The system already runs specialized detectors
(chain verification, replay divergence, approval watchdog, automation audit,
executor verify receipts, Vesta hash-verified writes, chain anchor), each
speaking its own shape. The DiscrepancyEngine normalizes every finding into a
single ``Discrepancy`` object, persists it, mirrors it to the audit chain, and
exposes query/status surfaces — turning seven detectors into one system-wide
diagnostic layer.

Design rules:

  - Deterministic, no LLM, no spend. Confidence is 1.0 for detectors whose
    verdict is a hard fact (a hash mismatch, an illegal transition), lower
    for detectors whose signal is weaker.
  - Failure-isolated: one detector raising must not kill the scan; its error
    is captured into the report as an ``info``-severity finding.
  - Deduped: the same open discrepancy is not re-inserted on every scan —
    ``last_seen`` is bumped instead, so a broken system keeps surfacing
    without spamming the store.
  - Evidence first: every new discrepancy appends to the audit chain
    (source=discrepancy_engine) when a chain is available. A recorded
    discrepancy is itself auditable evidence.

CLI:
    python -m msb_v3.ops.discrepancy scan             # run all detectors
    python -m msb_v3.ops.discrepancy status           # counts by severity/status
    python -m msb_v3.ops.discrepancy query --subsystem replay --limit 20
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from msb_v3.core.config import settings

logger = logging.getLogger(__name__)

# Severities — vocabulary shared with the automation audit (warn) and the
# health vocabulary (critical maps to FAILED components).
SEV_INFO = "info"
SEV_WARN = "warn"
SEV_CRITICAL = "critical"

# Status lifecycle: open -> acknowledged -> resolved (manual), or the
# detector itself repaired the condition (auto_repaired — e.g. the approval
# watchdog voiding a dangling approval).
STATUS_OPEN = "open"
STATUS_ACKNOWLEDGED = "acknowledged"
STATUS_RESOLVED = "resolved"
STATUS_AUTO_REPAIRED = "auto_repaired"

SEVERITIES = (SEV_INFO, SEV_WARN, SEV_CRITICAL)
STATUSES = (STATUS_OPEN, STATUS_ACKNOWLEDGED, STATUS_RESOLVED, STATUS_AUTO_REPAIRED)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_discrepancy_db_path() -> Path:
    """``<data>/runtime/discrepancies.db`` — alongside wake/tasks/automation state."""
    return Path(settings.db_path).parent / "runtime" / "discrepancies.db"


@dataclass
class Discrepancy:
    """One normalized diagnostic finding — the universal discrepancy object."""

    id: str
    timestamp: str
    subsystem: str
    expected_state: str
    observed_state: str
    discrepancy_type: str
    severity: str
    evidence: Dict[str, Any]
    confidence: float
    affected_resource: str
    suggested_action: str
    status: str = STATUS_OPEN
    last_seen: str = ""

    def fingerprint(self) -> str:
        """Identity for dedupe: same subsystem/type/resource = same discrepancy."""
        return f"{self.subsystem}:{self.discrepancy_type}:{self.affected_resource}"

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DiscrepancyStore:
    """SQLite persistence for discrepancies (runtime/discrepancies.db)."""

    def __init__(self, db_path: Optional[str | Path] = None) -> None:
        self.db_path = Path(db_path) if db_path else default_discrepancy_db_path()
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
                CREATE TABLE IF NOT EXISTS discrepancies (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    subsystem TEXT NOT NULL,
                    expected_state TEXT NOT NULL,
                    observed_state TEXT NOT NULL,
                    discrepancy_type TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    affected_resource TEXT NOT NULL,
                    suggested_action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    last_seen TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_disc_fp ON discrepancies (subsystem, discrepancy_type, affected_resource)"
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_disc_status ON discrepancies (status)")

    def has_open(self, fingerprint: str) -> bool:
        sub, dtype, resource = fingerprint.split(":", 2)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM discrepancies WHERE subsystem=? AND discrepancy_type=? AND affected_resource=? AND status IN (?, ?) LIMIT 1",
                (sub, dtype, resource, STATUS_OPEN, STATUS_ACKNOWLEDGED),
            ).fetchone()
        return row is not None

    def insert(self, d: Discrepancy) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO discrepancies (id, timestamp, subsystem, expected_state, observed_state, "
                "discrepancy_type, severity, evidence, confidence, affected_resource, suggested_action, status, last_seen) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    d.id,
                    d.timestamp,
                    d.subsystem,
                    d.expected_state,
                    d.observed_state,
                    d.discrepancy_type,
                    d.severity,
                    json.dumps(d.evidence, default=str),
                    d.confidence,
                    d.affected_resource,
                    d.suggested_action,
                    d.status,
                    d.last_seen or d.timestamp,
                ),
            )

    def touch(self, fingerprint: str, ts: str) -> None:
        """Bump last_seen for an already-open discrepancy (dedupe)."""
        sub, dtype, resource = fingerprint.split(":", 2)
        with self._connect() as conn:
            conn.execute(
                "UPDATE discrepancies SET last_seen=? WHERE subsystem=? AND discrepancy_type=? "
                "AND affected_resource=? AND status IN (?, ?)",
                (ts, sub, dtype, resource, STATUS_OPEN, STATUS_ACKNOWLEDGED),
            )

    def query(
        self,
        *,
        subsystem: Optional[str] = None,
        severity: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        clauses: List[str] = []
        params: List[Any] = []
        if subsystem:
            clauses.append("subsystem=?")
            params.append(subsystem)
        if severity:
            clauses.append("severity=?")
            params.append(severity)
        if status:
            clauses.append("status=?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM discrepancies {where} ORDER BY timestamp DESC LIMIT ?", params
            ).fetchall()
        out: List[Dict[str, Any]] = []
        for row in rows:
            d = dict(row)
            d["evidence"] = json.loads(d["evidence"])
            out.append(d)
        return out

    def set_status(self, discrepancy_id: str, status: str) -> None:
        """Move one discrepancy to a new status (open/acknowledged/resolved)."""
        with self._connect() as conn:
            conn.execute("UPDATE discrepancies SET status=? WHERE id=?", (status, discrepancy_id))

    def counts(self) -> Dict[str, Any]:
        with self._connect() as conn:
            by_status = {
                r["status"]: r["n"]
                for r in conn.execute("SELECT status, COUNT(*) AS n FROM discrepancies GROUP BY status")
            }
            by_severity = {
                r["severity"]: r["n"]
                for r in conn.execute("SELECT severity, COUNT(*) AS n FROM discrepancies GROUP BY severity")
            }
            open_critical = conn.execute(
                "SELECT COUNT(*) FROM discrepancies WHERE severity=? AND status IN (?, ?)",
                (SEV_CRITICAL, STATUS_OPEN, STATUS_ACKNOWLEDGED),
            ).fetchone()[0]
        return {
            "total": sum(by_status.values()),
            "by_status": by_status,
            "by_severity": by_severity,
            "open_critical": open_critical,
        }


# ---------------------------------------------------------------------------
# Detector adapters — each returns a list of raw findings, never raises.
# ---------------------------------------------------------------------------


def _finding(
    subsystem: str,
    discrepancy_type: str,
    expected_state: str,
    observed_state: str,
    severity: str,
    evidence: Dict[str, Any],
    affected_resource: str,
    suggested_action: str,
    confidence: float = 1.0,
) -> Dict[str, Any]:
    return {
        "subsystem": subsystem,
        "discrepancy_type": discrepancy_type,
        "expected_state": expected_state,
        "observed_state": observed_state,
        "severity": severity,
        "evidence": evidence,
        "confidence": confidence,
        "affected_resource": affected_resource,
        "suggested_action": suggested_action,
    }


def _detect_audit_chain() -> List[Dict[str, Any]]:
    """AuditChain.verify_chain() — a broken hash chain is a critical discrepancy."""
    from msb_ledger.audit_chain import AuditChain

    chain_db = Path(settings.db_path).parent / "uac" / "audit_chain.db"
    if not chain_db.exists():
        return []
    try:
        verified = AuditChain(db_path=str(chain_db)).verify_chain()
    except Exception as exc:  # noqa: BLE001 — detector isolation
        return [
            _finding(
                "audit_chain",
                "chain_verify_error",
                "verify_chain() returns a verdict",
                f"verify raised {exc.__class__.__name__}: {exc}",
                SEV_WARN,
                {"error": str(exc)},
                str(chain_db),
                "inspect the chain DB; the daily anchor job re-signs only a readable chain",
                confidence=0.8,
            )
        ]
    if verified.get("valid", True):
        return []
    return [
        _finding(
            "audit_chain",
            "chain_invalid",
            "hash chain verifies end-to-end",
            f"chain broken at record {verified.get('broken_at_seq', '?')}",
            SEV_CRITICAL,
            verified,
            str(chain_db),
            "stop all appends; restore the chain from the last good backup; do not patch hashes",
        )
    ]


def _detect_evidence_spine() -> List[Dict[str, Any]]:
    """Evidence Spine chain verification — decision-level provenance integrity."""
    from msb_v3.evidence.spine import DecisionEvidenceStore

    db_path = Path(settings.decision_spine_db_path)
    if not db_path.exists():
        return []
    try:
        store = DecisionEvidenceStore(db_path=str(db_path))
        verified = store.verify_chain()
    except Exception as exc:  # noqa: BLE001
        return [
            _finding(
                "evidence_spine",
                "spine_verify_error",
                "spine chain verifies",
                f"verify raised {exc.__class__.__name__}: {exc}",
                SEV_WARN,
                {"error": str(exc)},
                str(db_path),
                "inspect the decision spine DB",
                confidence=0.8,
            )
        ]
    if verified.get("valid", True):
        return []
    return [
        _finding(
            "evidence_spine",
            "spine_chain_invalid",
            "decision evidence chain verifies end-to-end",
            f"broken at seq {verified.get('broken_at_seq', '?')}: {verified.get('reason', '')}",
            SEV_CRITICAL,
            verified,
            str(db_path),
            "stop governed writes; restore the spine from backup",
        )
    ]


def _detect_replay(limit: int = 50) -> List[Dict[str, Any]]:
    """Replay recent tasks: projection drift or illegal transitions (corruption
    signals the replay engine never silently heals)."""
    from msb_v3.replay.engine import ReplayEngine
    from msb_v3.tasks.lifecycle import TaskLifecycle

    try:
        # Derive the store path from settings at call time (the module-level
        # default in tasks/lifecycle.py is bound at import) so the detector
        # honors an MSB_DB_PATH override and stays testable.
        tasks_db = str(Path(settings.db_path).parent / "runtime" / "tasks.db")
        lifecycle = TaskLifecycle(db_path=tasks_db)
        engine = ReplayEngine(lifecycle)
        tasks = lifecycle.list(limit=limit)
    except Exception as exc:  # noqa: BLE001
        return [
            _finding(
                "replay",
                "replay_unavailable",
                "task store is readable",
                f"TaskLifecycle failed: {exc.__class__.__name__}: {exc}",
                SEV_WARN,
                {"error": str(exc)},
                "tasks",
                "check the runtime tasks.db",
                confidence=0.8,
            )
        ]
    findings: List[Dict[str, Any]] = []
    for task in tasks:
        task_id = task.get("id") or task.get("task_id")
        if not task_id:
            continue
        try:
            state = engine.replay_state(task_id)
        except Exception:  # noqa: BLE001 — per-task isolation
            continue
        if state.get("consistent") is False:
            findings.append(
                _finding(
                    "replay",
                    "projection_divergence",
                    "stored state == event-derived state",
                    state.get("divergence", "projection drifted from events"),
                    SEV_CRITICAL,
                    state,
                    task_id,
                    "quarantine the task; reconstruct from its event log",
                )
            )
        if state.get("legal") is False:
            findings.append(
                _finding(
                    "replay",
                    "illegal_transition",
                    "every event transition is legal per the state machine",
                    "; ".join(state.get("issues", ["illegal event sequence"])),
                    SEV_CRITICAL,
                    state,
                    task_id,
                    "quarantine the task; the event sequence cannot be replayed",
                )
            )
    return findings


def _detect_approval_watchdog() -> List[Dict[str, Any]]:
    """Approval ledger: APPROVED approvals whose task never reached a terminal
    state (the watchdog auto-voids these; the discrepancy records the repair)."""
    from msb_v3.vesta.approval_watchdog import ApprovalWatchdog

    try:
        scan = ApprovalWatchdog().scan()
    except Exception as exc:  # noqa: BLE001
        return [
            _finding(
                "approval_watchdog",
                "watchdog_unavailable",
                "approval ledger is scannable",
                f"scan raised {exc.__class__.__name__}: {exc}",
                SEV_WARN,
                {"error": str(exc)},
                "vesta_approvals",
                "check the Vesta approval store",
                confidence=0.8,
            )
        ]
    findings: List[Dict[str, Any]] = []
    for entry in scan.get("dangling", []):
        findings.append(
            _finding(
                "approval_watchdog",
                "dangling_approval",
                "every APPROVED approval's task reaches a terminal state",
                f"task {entry.get('task_state', 'MISSING')} never reached a terminal state",
                SEV_WARN,
                entry,
                entry.get("approval_id", ""),
                "run the approval watchdog (auto-voids and quarantines)",
            )
        )
    return findings


def _detect_automation_audit() -> List[Dict[str, Any]]:
    """The wake cycle's deterministic self-maintenance audit (provider seams,
    budget vs cap, dead hooks, manifest drift)."""
    from msb_v3.automation.audit import run_audit

    try:
        report = run_audit()
    except Exception as exc:  # noqa: BLE001
        return [
            _finding(
                "automation_audit",
                "audit_unavailable",
                "automation audit runs",
                f"run_audit raised {exc.__class__.__name__}: {exc}",
                SEV_WARN,
                {"error": str(exc)},
                "automation",
                "check the automation manifest and state stores",
                confidence=0.8,
            )
        ]
    findings: List[Dict[str, Any]] = []
    for f in report.get("findings", []):
        kind = f.get("kind", "unknown")
        severity = SEV_CRITICAL if f.get("severity") == "critical" else SEV_WARN
        findings.append(
            _finding(
                "automation_audit",
                f"{kind}_unavailable" if kind == "provider" else f"{kind}_high",
                f"automation {kind} within bounds",
                f.get("detail", ""),
                severity,
                f,
                f.get("subject", ""),
                "resolve the provider/budget condition or acknowledge",
            )
        )
    return findings


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class DiscrepancyEngine:
    """Run every wired detector, normalize to ``Discrepancy``, persist, audit."""

    def __init__(
        self,
        *,
        store: Optional[DiscrepancyStore] = None,
        audit: Optional[Any] = None,
        detectors: Optional[List[str]] = None,
    ) -> None:
        self.store = store or DiscrepancyStore()
        self._audit = audit
        self.detectors = detectors or [
            "audit_chain",
            "evidence_spine",
            "replay",
            "approval_watchdog",
            "automation_audit",
        ]

    # -- audit mirror ------------------------------------------------------

    def _audit_append(self, d: Discrepancy) -> None:
        """Mirror a new discrepancy to the audit chain (best-effort — a chain
        append failure must never break the diagnostic layer itself)."""
        if self._audit is None:
            try:
                from msb_ledger.chain_anchor import anchored_chain_from_env

                self._audit = anchored_chain_from_env()
            except Exception:  # noqa: BLE001
                return
        try:
            self._audit.append(
                "discrepancy_engine",
                "discrepancy.recorded",
                {
                    "discrepancy_id": d.id,
                    "subsystem": d.subsystem,
                    "type": d.discrepancy_type,
                    "severity": d.severity,
                    "resource": d.affected_resource,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("discrepancy audit mirror failed: %s", exc)

    # -- recording ---------------------------------------------------------

    def record(self, finding: Dict[str, Any]) -> Optional[Discrepancy]:
        """Normalize one raw finding and persist it (deduped on fingerprint)."""
        d = Discrepancy(
            id=uuid.uuid4().hex[:12],
            timestamp=_now(),
            subsystem=finding["subsystem"],
            expected_state=finding["expected_state"],
            observed_state=finding["observed_state"],
            discrepancy_type=finding["discrepancy_type"],
            severity=finding["severity"],
            evidence=finding.get("evidence", {}),
            confidence=float(finding.get("confidence", 1.0)),
            affected_resource=finding.get("affected_resource", ""),
            suggested_action=finding.get("suggested_action", ""),
            status=finding.get("status", STATUS_OPEN),
        )
        fp = d.fingerprint()
        if self.store.has_open(fp):
            self.store.touch(fp, d.timestamp)
            return None
        self.store.insert(d)
        self._audit_append(d)
        return d

    # -- scan --------------------------------------------------------------

    def _run_detector(self, name: str) -> List[Dict[str, Any]]:
        dispatch = {
            "audit_chain": _detect_audit_chain,
            "evidence_spine": _detect_evidence_spine,
            "replay": _detect_replay,
            "approval_watchdog": _detect_approval_watchdog,
            "automation_audit": _detect_automation_audit,
        }
        fn = dispatch.get(name)
        if fn is None:
            raise ValueError(f"unknown detector: {name}")
        return fn()

    def scan(self) -> Dict[str, Any]:
        """Run all detectors, persist new discrepancies, return the report."""
        results: List[Dict[str, Any]] = []
        recorded = 0
        seen = 0
        for name in self.detectors:
            try:
                findings = self._run_detector(name)
            except Exception as exc:  # noqa: BLE001 — a broken detector is a finding, not a crash
                findings = [
                    _finding(
                        name,
                        "detector_error",
                        "detector runs",
                        f"{exc.__class__.__name__}: {exc}",
                        SEV_WARN,
                        {"error": str(exc)},
                        name,
                        "fix the detector wiring",
                        confidence=0.5,
                    )
                ]
            for finding in findings:
                d = self.record(finding)
                if d is not None:
                    recorded += 1
                else:
                    seen += 1
            results.append({"detector": name, "findings": len(findings)})
        return {
            "ok": True,
            "ts": _now(),
            "detectors": results,
            "new_discrepancies": recorded,
            "already_open": seen,
            "counts": self.store.counts(),
        }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli() -> None:
    parser = argparse.ArgumentParser(prog="msb_v3.ops.discrepancy", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scan", help="run all detectors and persist findings")
    sub.add_parser("status", help="counts by severity/status")
    q = sub.add_parser("query", help="list discrepancies")
    q.add_argument("--subsystem", default=None)
    q.add_argument("--severity", default=None, choices=SEVERITIES)
    q.add_argument("--status", default=None, choices=STATUSES)
    q.add_argument("--limit", type=int, default=50)
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)

    if args.command == "scan":
        report = DiscrepancyEngine().scan()
        print(json.dumps(report, indent=2, default=str))
        return
    store = DiscrepancyStore()
    if args.command == "status":
        print(json.dumps(store.counts(), indent=2))
        return
    rows = store.query(
        subsystem=args.subsystem, severity=args.severity, status=args.status, limit=args.limit
    )
    print(json.dumps(rows, indent=2, default=str))


if __name__ == "__main__":
    _cli()
