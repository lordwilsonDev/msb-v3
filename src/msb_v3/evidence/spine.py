"""Evidence Spine — one structured, causally-linked record per governed decision.

Complements (does not duplicate) the existing provenance stack:

- ``uac/audit_chain.py`` — the immutable hash chain of *events* (append-only,
  externally anchorable). The spine cross-references it via ``audit_seq``.
- ``vesta/evidence.py`` — content-addressed evidence *blobs* (``ev_<sha256>``).
  The spine references them via ``evidence_refs``.

The spine's job is the missing middle: a single ``DecisionEvidence`` record that
answers *WHO did WHAT, WHEN, under WHICH policy, with WHICH authority, against
WHICH resource, with WHAT result, how verified* — linked forward into evidence
and backward into the audit chain, with content-addressing (``content_hash``)
and a causal parent chain (``parent_hash``).

Integrity model (mirrors AuditChain, same guarantees):

- ``parent_hash`` — the previous spine record's ``content_hash`` (genesis = 64
  zeros), so the causal order is tamper-evident.
- ``content_hash`` — sha256 over the canonical evidence fields *plus* the
  parent hash, so any edit to any record breaks every hash after it.
- ``audit_seq`` — the AuditChain sequence of the event that recorded this same
  decision, so a spine record is independently traceable into the immutable
  audit chain.

Captures structured decision provenance only — never private chain-of-thought.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from msb_v3.core.config import settings

_GENESIS_HASH = "0" * 64

# Fields whose dataclass type is a tuple but whose canonical JSON form is a
# sorted list (deterministic content-addressing regardless of input order).
_TUPLE_FIELDS = frozenset(
    {
        "capability_requested",
        "capability_granted",
        "evidence_refs",
        "context_refs",
        "available_actions",
    }
)


class SpineError(RuntimeError):
    """Raised when a spine record cannot be safely stored or verified."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path(value: Optional[str]) -> Path:
    path = Path(value or settings.decision_spine_db_path)
    return path if path.is_absolute() else Path(settings.msb_home) / path


def _sha256_canonical(obj: Any) -> str:
    canonical = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DecisionEvidence:
    """One governed decision, as structured provenance.

    Every field is optional except the core identity/policy/capability fields;
    a caller fills what it knows at decision time and leaves the rest None/()
    for a later (execution/verification) record to link. Private
    chain-of-thought is never captured here — only structured decision inputs
    and outcomes.
    """

    task_id: str
    policy_version: str
    policy_result: str
    risk_level: str
    capability_requested: tuple[str, ...] = ()
    capability_granted: tuple[str, ...] = ()
    timestamp: str = field(default_factory=_now_iso)
    mission_id: Optional[str] = None
    agent_id: Optional[str] = None
    tenant_id: Optional[str] = None
    model_id: Optional[str] = None
    model_version: Optional[str] = None
    provider: Optional[str] = None
    evidence_refs: tuple[str, ...] = ()
    context_refs: tuple[str, ...] = ()
    selected_action: Optional[str] = None
    available_actions: tuple[str, ...] = ()
    uncertainty: Optional[float] = None
    approval_required: bool = False
    approval_id: Optional[str] = None
    execution_id: Optional[str] = None
    result_id: Optional[str] = None
    verification_id: Optional[str] = None

    def to_fields(self) -> dict[str, Any]:
        """Canonical, JSON-ready field map (tuples -> sorted lists)."""
        return {
            "mission_id": self.mission_id,
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "tenant_id": self.tenant_id,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "provider": self.provider,
            "capability_requested": sorted(self.capability_requested),
            "capability_granted": sorted(self.capability_granted),
            "policy_version": self.policy_version,
            "policy_result": self.policy_result,
            "evidence_refs": sorted(self.evidence_refs),
            "context_refs": sorted(self.context_refs),
            "selected_action": self.selected_action,
            "available_actions": sorted(self.available_actions),
            "risk_level": self.risk_level,
            "uncertainty": self.uncertainty,
            "approval_required": self.approval_required,
            "approval_id": self.approval_id,
            "execution_id": self.execution_id,
            "result_id": self.result_id,
            "verification_id": self.verification_id,
            "timestamp": self.timestamp,
        }


@dataclass(frozen=True)
class DecisionEvidenceRecord:
    """A stored spine record: the evidence plus its integrity/linkage fields."""

    seq: int
    decision_id: str
    content_hash: str
    parent_hash: str
    audit_seq: Optional[int]
    evidence: DecisionEvidence

    def as_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "decision_id": self.decision_id,
            "content_hash": self.content_hash,
            "parent_hash": self.parent_hash,
            "audit_seq": self.audit_seq,
            **self.evidence.to_fields(),
        }


def compute_content_hash(parent_hash: str, evidence: DecisionEvidence) -> str:
    """sha256 over the canonical evidence fields plus the parent hash."""
    return _sha256_canonical({"parent_hash": parent_hash, **evidence.to_fields()})


def _evidence_from_fields(fields: dict[str, Any]) -> DecisionEvidence:
    kwargs: dict[str, Any] = {}
    for key, value in fields.items():
        if key in _TUPLE_FIELDS and isinstance(value, list):
            kwargs[key] = tuple(value)
        else:
            kwargs[key] = value
    return DecisionEvidence(**kwargs)


class DecisionEvidenceStore:
    """Append-only, hash-chained store of DecisionEvidence records.

    Not a replacement for the AuditChain — it is the decision-level index over
    it. Each record carries the ``audit_seq`` of the audit event that recorded
    the same decision, so the two chains cross-reference each other.
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = _db_path(db_path)
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
                CREATE TABLE IF NOT EXISTS decision_evidence (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    decision_id TEXT NOT NULL UNIQUE,
                    content_hash TEXT NOT NULL,
                    parent_hash TEXT NOT NULL,
                    audit_seq INTEGER,
                    task_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_decision_task ON decision_evidence(task_id)"
            )

    def _last_hash(self, conn: sqlite3.Connection) -> str:
        row = conn.execute(
            "SELECT content_hash FROM decision_evidence ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return row["content_hash"] if row else _GENESIS_HASH

    def append(
        self,
        evidence: DecisionEvidence,
        audit_seq: Optional[int] = None,
    ) -> DecisionEvidenceRecord:
        """Store one decision, chain-linked to its parent and cross-linked to
        the audit chain. Returns the stored record with its hashes."""
        payload = evidence.to_fields()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            parent_hash = self._last_hash(conn)
            content_hash = compute_content_hash(parent_hash, evidence)
            decision_id = f"decision_{uuid.uuid4().hex[:12]}"
            conn.execute(
                """
                INSERT INTO decision_evidence(
                    decision_id, content_hash, parent_hash, audit_seq,
                    task_id, payload, timestamp
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    decision_id,
                    content_hash,
                    parent_hash,
                    audit_seq,
                    evidence.task_id,
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                    evidence.timestamp,
                ),
            )
        return self.get(decision_id)

    def _row_to_record(self, row: sqlite3.Row) -> DecisionEvidenceRecord:
        fields = json.loads(row["payload"])
        return DecisionEvidenceRecord(
            seq=row["seq"],
            decision_id=row["decision_id"],
            content_hash=row["content_hash"],
            parent_hash=row["parent_hash"],
            audit_seq=row["audit_seq"],
            evidence=_evidence_from_fields(fields),
        )

    def get(self, decision_id: str) -> DecisionEvidenceRecord:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM decision_evidence WHERE decision_id=?", (decision_id,)
            ).fetchone()
        if row is None:
            raise SpineError("unknown decision evidence record")
        return self._row_to_record(row)

    def recent(self, limit: int = 100) -> list[DecisionEvidenceRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM decision_evidence ORDER BY seq DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def trail(self, task_id: str) -> list[DecisionEvidenceRecord]:
        """The causal spine for one task, in append order — the backbone of a
        WHO/WHAT/WHEN/WHY reconstruction."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM decision_evidence WHERE task_id=? ORDER BY seq ASC",
                (task_id,),
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def verify_chain(self) -> dict[str, Any]:
        """Recompute every content hash and check parent linkage. Returns the
        first break (like AuditChain.verify_chain), never silently heals."""
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM decision_evidence ORDER BY seq ASC").fetchall()
        expected_parent = _GENESIS_HASH
        for row in rows:
            fields = json.loads(row["payload"])
            evidence = _evidence_from_fields(fields)
            if row["parent_hash"] != expected_parent:
                return {
                    "valid": False,
                    "broken_at_seq": row["seq"],
                    "reason": "parent_hash does not match preceding record",
                }
            recomputed = compute_content_hash(row["parent_hash"], evidence)
            if recomputed != row["content_hash"]:
                return {
                    "valid": False,
                    "broken_at_seq": row["seq"],
                    "reason": "stored content_hash does not match recomputed hash",
                }
            expected_parent = row["content_hash"]
        return {"valid": True, "record_count": len(rows)}
