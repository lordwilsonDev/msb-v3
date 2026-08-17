"""Axiom Library — versioned, evidence-backed artifact store for UAC.

Referenced by the Sovereign Organism Completion Blueprint (Phase A1-A4) and
the UAC Stage 0 spec as the durable home for compiler deliverables. Did not
exist anywhere in the codebase before this module — confirmed by direct
search during the Stage 0 build (2026-08-02). Not one of the six named
Sovereign Runtime components in the frozen UAC v1.0 spec (SAC, Guardian,
Merkle Audit Chain, Hardware Attestation, Observer's Log, STAR Scheduler);
best understood as the persistence layer backing Cross-Cutting System 2
(Universal Manifest) — "every stage can restart from the manifest" requires
somewhere for the manifest to actually live.

Minimal by design, per the 2026-08-02 build decision: versioned storage and
retrieval by ID/stage/profession. No full-text search (FTS5) yet — flagged
as a future enhancement, not built now.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from msb_ledger.config import settings

_RUNTIME_ROOT = Path(settings.db_path).parent / "uac"
_AXIOM_DB = _RUNTIME_ROOT / "axiom_library.db"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                artifact_id TEXT NOT NULL,
                stage TEXT NOT NULL,
                version TEXT NOT NULL,
                profession TEXT,
                jurisdiction TEXT,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(artifact_id, version)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_stage ON artifacts(stage)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_artifacts_profession ON artifacts(profession)")


@dataclass
class ArtifactRecord:
    artifact_id: str
    stage: str
    version: str
    payload: Dict[str, Any]
    profession: Optional[str] = None
    jurisdiction: Optional[str] = None
    created_at: Optional[str] = None


class AxiomLibrary:
    """Versioned artifact store. One row per (artifact_id, version)."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = Path(db_path) if db_path else _AXIOM_DB
        _init_db(self.db_path)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def publish(self, record: ArtifactRecord) -> ArtifactRecord:
        """Store an artifact version. Raises if (artifact_id, version) already exists —
        artifacts are immutable once published; publish a new version instead of overwriting."""
        created_at = record.created_at or _now_iso()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO artifacts(artifact_id, stage, version, profession, jurisdiction, payload, created_at)
                VALUES (?,?,?,?,?,?,?)
                """,
                (
                    record.artifact_id,
                    record.stage,
                    record.version,
                    record.profession,
                    record.jurisdiction,
                    json.dumps(record.payload, ensure_ascii=False),
                    created_at,
                ),
            )
        return ArtifactRecord(
            artifact_id=record.artifact_id,
            stage=record.stage,
            version=record.version,
            payload=record.payload,
            profession=record.profession,
            jurisdiction=record.jurisdiction,
            created_at=created_at,
        )

    def get(self, artifact_id: str, version: Optional[str] = None) -> Optional[ArtifactRecord]:
        """Fetch a specific version, or the latest version if version is omitted."""
        with self._conn() as conn:
            if version:
                row = conn.execute(
                    "SELECT * FROM artifacts WHERE artifact_id=? AND version=?",
                    (artifact_id, version),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM artifacts WHERE artifact_id=? ORDER BY created_at DESC LIMIT 1",
                    (artifact_id,),
                ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def list_versions(self, artifact_id: str) -> List[ArtifactRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM artifacts WHERE artifact_id=? ORDER BY created_at ASC",
                (artifact_id,),
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def list_by_stage(self, stage: str) -> List[ArtifactRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM artifacts WHERE stage=? ORDER BY created_at DESC",
                (stage,),
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def list_by_profession(self, profession: str) -> List[ArtifactRecord]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM artifacts WHERE profession=? ORDER BY created_at DESC",
                (profession,),
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> ArtifactRecord:
        return ArtifactRecord(
            artifact_id=row["artifact_id"],
            stage=row["stage"],
            version=row["version"],
            payload=json.loads(row["payload"]),
            profession=row["profession"],
            jurisdiction=row["jurisdiction"],
            created_at=row["created_at"],
        )
