"""Read-only export of historical v1 audit records.

The exporter never imports the mutable :class:`AuditChain` connection path.  It
opens SQLite with ``mode=ro`` and recomputes the historical v1 hash algorithm so
an independent copy can be verified without trusting the producer process.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

EXPORT_SCHEMA = "msb.audit.v1-export"
MANIFEST_SCHEMA = "msb.audit.v1-export-manifest"
GENESIS_HASH = "0" * 64
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class ExportError(RuntimeError):
    """The source chain could not be exported safely."""


@dataclass(frozen=True)
class ExportResult:
    output_path: Path
    manifest_path: Path
    manifest: dict[str, Any]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_fingerprint(path: Path) -> dict[str, tuple[int, int, int, str] | None]:
    """Fingerprint the main database and any SQLite journal sidecars."""
    result: dict[str, tuple[int, int, int, str] | None] = {}
    for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
        key = candidate.name
        try:
            stat = candidate.stat()
            result[key] = (stat.st_size, stat.st_mtime_ns, stat.st_ino, _file_digest(candidate))
        except FileNotFoundError:
            result[key] = None
    return result


def _open_read_only(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise ExportError(f"audit database does not exist: {path}")
    uri = f"file:{quote(path.resolve().as_posix(), safe='/')}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=10.0)
    except sqlite3.Error as exc:
        raise ExportError(f"cannot open audit database read-only: {exc}") from exc
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _v1_hash(prev_hash: str, component: str, event_type: str, payload: Any, timestamp: str) -> str:
    """Recompute the historical AuditChain v1 hash exactly."""
    canonical = json.dumps(
        {
            "prev_hash": prev_hash,
            "component": component,
            "event_type": event_type,
            "payload": payload,
            "timestamp": timestamp,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _export_record(row: sqlite3.Row) -> dict[str, Any]:
    try:
        payload = json.loads(row["payload"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise ExportError(f"malformed payload at seq {row['seq']}: {exc}") from exc
    record = {
        "schema_version": EXPORT_SCHEMA,
        "seq": row["seq"],
        "component": row["component"],
        "event_type": row["event_type"],
        "payload": payload,
        "timestamp": row["timestamp"],
        "prev_hash": row["prev_hash"],
        "record_hash": row["record_hash"],
    }
    if not isinstance(row["seq"], int) or row["seq"] < 1:
        raise ExportError(f"invalid sequence at row {row['seq']!r}")
    if not isinstance(row["prev_hash"], str) or not _HASH_RE.fullmatch(row["prev_hash"]):
        raise ExportError(f"invalid prev_hash at seq {row['seq']}")
    if not isinstance(row["record_hash"], str) or not _HASH_RE.fullmatch(row["record_hash"]):
        raise ExportError(f"invalid record_hash at seq {row['seq']}")
    expected = _v1_hash(
        record["prev_hash"],
        record["component"],
        record["event_type"],
        record["payload"],
        record["timestamp"],
    )
    if expected != record["record_hash"]:
        raise ExportError(f"record hash mismatch at seq {row['seq']}")
    return record


def _verify_rows(rows: Iterable[sqlite3.Row]) -> tuple[list[dict[str, Any]], str, int]:
    records: list[dict[str, Any]] = []
    expected_seq = 1
    expected_prev = GENESIS_HASH
    for row in rows:
        record = _export_record(row)
        if record["seq"] != expected_seq:
            raise ExportError(f"sequence gap: expected {expected_seq}, got {record['seq']}")
        if record["prev_hash"] != expected_prev:
            raise ExportError(f"prev_hash mismatch at seq {record['seq']}")
        records.append(record)
        expected_seq += 1
        expected_prev = record["record_hash"]
    if not records:
        raise ExportError("audit chain is empty; refusing to create a false export manifest")
    return records, expected_prev, len(records)


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return _file_digest(path)


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def export_v1(database: str | Path, output: str | Path, manifest: str | Path) -> ExportResult:
    """Export one unchanged v1 SQLite chain to JSONL and a durable manifest."""
    database_path = Path(database)
    output_path = Path(output)
    manifest_path = Path(manifest)
    if output_path.resolve() == database_path.resolve():
        raise ExportError("output must not overwrite the source database")
    if manifest_path.resolve() == database_path.resolve():
        raise ExportError("manifest must not overwrite the source database")
    if output_path.resolve() == manifest_path.resolve():
        raise ExportError("output and manifest must be different files")
    if not database_path.exists():
        raise ExportError(f"audit database does not exist: {database_path}")

    before_fingerprint = _source_fingerprint(database_path)
    try:
        with _open_read_only(database_path) as conn:
            rows = conn.execute("SELECT * FROM audit_records ORDER BY seq ASC").fetchall()
    except sqlite3.DatabaseError as exc:
        raise ExportError(f"cannot read audit database: {exc}") from exc
    records, head_hash, count = _verify_rows(rows)
    after_fingerprint = _source_fingerprint(database_path)
    if before_fingerprint != after_fingerprint:
        raise ExportError("source database changed during export; refusing partial evidence")
    after_stat = database_path.stat()
    after_digest = _file_digest(database_path)

    output_digest = _write_jsonl(output_path, records)
    result_manifest = {
        "schema_version": MANIFEST_SCHEMA,
        "source_db": str(database_path.resolve()),
        "source_db_sha256": after_digest,
        "source_db_size": after_stat.st_size,
        "source_db_mtime_ns": after_stat.st_mtime_ns,
        "first_seq": records[0]["seq"],
        "last_seq": records[-1]["seq"],
        "record_count": count,
        "first_prev_hash": records[0]["prev_hash"],
        "head_hash": head_hash,
        "output_path": str(output_path.resolve()),
        "output_sha256": output_digest,
        "exported_at": _now_iso(),
        "source_mutation_during_export": False,
    }
    _write_manifest(manifest_path, result_manifest)
    return ExportResult(output_path, manifest_path, result_manifest)


def _main() -> int:
    parser = argparse.ArgumentParser(description="Export a read-only historical v1 audit chain")
    parser.add_argument("--database", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = export_v1(args.database, args.output, args.manifest)
    except (ExportError, OSError, sqlite3.Error) as exc:
        print(f"EXPORT FAILED: {exc}")
        return 2
    print(json.dumps(result.manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
