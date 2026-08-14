"""Content-addressed evidence objects for the Vesta forensic boundary."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from msb_v3.core.config import settings


class EvidenceError(RuntimeError):
    """Raised when evidence cannot be safely stored or verified."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _path(value: Optional[str], default: str) -> Path:
    path = Path(value or default)
    return path if path.is_absolute() else Path(settings.msb_home) / path


class EvidenceStore:
    """Append-only metadata plus content-addressed local evidence blobs."""

    def __init__(self, root: Optional[str] = None, db_path: Optional[str] = None) -> None:
        self.root = _path(root, settings.vesta_evidence_root)
        self.db_path = _path(db_path, settings.vesta_evidence_db_path)
        self.root.mkdir(parents=True, exist_ok=True)
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
                CREATE TABLE IF NOT EXISTS vesta_evidence (
                    evidence_id TEXT PRIMARY KEY,
                    evidence_type TEXT NOT NULL,
                    sha256 TEXT NOT NULL UNIQUE,
                    size_bytes INTEGER NOT NULL,
                    relative_path TEXT NOT NULL,
                    captured_at TEXT NOT NULL,
                    metadata TEXT NOT NULL
                )
                """
            )

    def record_bytes(
        self,
        content: bytes,
        evidence_type: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not evidence_type or len(evidence_type) > 128:
            raise EvidenceError("evidence_type must be non-empty and short")
        digest = hashlib.sha256(content).hexdigest()
        evidence_id = f"ev_{digest}"
        relative_path = str(Path(digest[:2]) / digest)
        target = self.root / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if self._hash_file(target) != digest:
                raise EvidenceError("existing evidence object failed hash verification")
        else:
            temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
            try:
                with temporary.open("wb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temporary, 0o600)
                os.replace(temporary, target)
            finally:
                temporary.unlink(missing_ok=True)

        captured_at = _now()
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO vesta_evidence(
                    evidence_id, evidence_type, sha256, size_bytes,
                    relative_path, captured_at, metadata
                ) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(evidence_id) DO NOTHING
                """,
                (evidence_id, evidence_type, digest, len(content), relative_path, captured_at, metadata_json),
            )
        return self.get(evidence_id)

    def record_json(
        self,
        value: Any,
        evidence_type: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        content = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return self.record_bytes(content, evidence_type, metadata)

    def get(self, evidence_id: str) -> Dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM vesta_evidence WHERE evidence_id=?", (evidence_id,)).fetchone()
        if row is None:
            raise EvidenceError("unknown evidence object")
        path = self.root / row["relative_path"]
        verified = path.is_file() and self._hash_file(path) == row["sha256"]
        return {
            "evidence_id": row["evidence_id"],
            "evidence_type": row["evidence_type"],
            "sha256": row["sha256"],
            "size_bytes": row["size_bytes"],
            "relative_path": row["relative_path"],
            "captured_at": row["captured_at"],
            "metadata": json.loads(row["metadata"]),
            "verified": verified,
        }

    def read_bytes(self, evidence_id: str) -> bytes:
        metadata = self.get(evidence_id)
        if not metadata["verified"]:
            raise EvidenceError("evidence object failed hash verification")
        path = self.root / str(metadata["relative_path"])
        try:
            return path.read_bytes()
        except OSError as exc:
            raise EvidenceError("evidence object is unreadable") from exc

    def manifest(self, evidence_ids: Iterable[str]) -> list[Dict[str, Any]]:
        return [self.get(evidence_id) for evidence_id in evidence_ids]

    @staticmethod
    def _hash_file(path: Path) -> str:
        digest = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError as exc:
            raise EvidenceError("evidence object is unreadable") from exc
        return digest.hexdigest()
