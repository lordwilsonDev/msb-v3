"""Controlled v1-to-v2 audit migration barrier.

The barrier is intentionally conservative: it stops new v1 writes, exports and
verifies the old chain, requires an independently verified v1 head, then starts
one linked v2 segment.  Any failure leaves the live chain on verified v1 and
records a blocked migration manifest.
"""

from __future__ import annotations

import fcntl
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from msb_ledger.audit_chain import AuditChain
from msb_ledger.audit_v1_export import ExportError, export_v1
from msb_ledger.audit_v2 import compute_record_hash, iter_jsonl, verify_jsonl

MANIFEST_SCHEMA = "msb.audit.migration-manifest.v1"


class MigrationError(RuntimeError):
    """Migration cannot proceed or must remain blocked."""


class MigrationBlocked(MigrationError):
    """The migration has evidence of failure and must not be activated."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


class V2Segment:
    """Append-only JSONL segment for v2 records."""

    def __init__(self, path: str | Path, initial_prev_hash: str) -> None:
        self.path = Path(path)
        self.initial_prev_hash = initial_prev_hash
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.touch()

    def _records(self) -> list[dict[str, Any]]:
        if self.path.stat().st_size == 0:
            return []
        return list(iter_jsonl(self.path))

    def head(self) -> tuple[int, str]:
        records = self._records()
        if not records:
            return 0, self.initial_prev_hash
        return records[-1]["seq"], records[-1]["record_hash"]

    def verify(self) -> dict[str, Any]:
        if self.path.stat().st_size == 0:
            return {"valid": False, "record_count": 0, "reason": "v2 segment is empty"}
        try:
            result = verify_jsonl(self.path, initial_prev_hash=self.initial_prev_hash)
            records = self._records()
        except (OSError, ValueError, TypeError, KeyError) as exc:
            return {"valid": False, "record_count": 0, "reason": str(exc)}
        if result.get("valid") and records and records[0]["prev_hash"] != self.initial_prev_hash:
            return {
                "valid": False,
                "record_count": result.get("record_count", 0),
                "reason": "v2 segment does not link to the v1 head",
            }
        return result

    def append(
        self,
        *,
        component: str,
        event_type: str,
        payload: dict[str, Any],
        actor: str,
        action: str,
        authority: dict[str, Any],
        execution: dict[str, Any],
        verification: dict[str, Any],
        environment_ref: str,
    ) -> dict[str, Any]:
        lock_path = self.path.with_name(f".{self.path.name}.lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                records = self._records()
                next_seq = records[-1]["seq"] + 1 if records else 1
                prev_hash = records[-1]["record_hash"] if records else self.initial_prev_hash
                record = {
                    "schema_version": "msb.audit.v2",
                    "record_type": event_type,
                    "seq": next_seq,
                    "recorded_at": _now_iso(),
                    "component": component,
                    "actor": actor,
                    "action": action,
                    "authority": authority,
                    "execution": execution,
                    "verification": verification,
                    "payload": payload,
                    "environment_ref": environment_ref,
                    "prev_hash": prev_hash,
                    "record_hash": "0" * 64,
                }
                record["record_hash"] = compute_record_hash(record)
                with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                return record
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


class MigrationBarrier:
    """Coordinate a v1 export, signed-head gate, and first v2 record."""

    def __init__(
        self,
        chain: AuditChain,
        *,
        export_dir: str | Path,
        v2_path: str | Path,
        head_verifier: Callable[[AuditChain], dict[str, Any]],
        operator: str = "system:migration",
    ) -> None:
        if not operator.strip():
            raise ValueError("operator is required")
        self.chain = chain
        self.export_dir = Path(export_dir)
        self.v2_path = Path(v2_path)
        self.head_verifier = head_verifier
        self.operator = operator
        self.manifest_path = self.export_dir / "migration-manifest.json"
        self.lock_path = self.chain.db_path.with_name(f".{self.chain.db_path.name}.migration.lock")

    def _manifest(self, **values: Any) -> dict[str, Any]:
        return {
            "schema_version": MANIFEST_SCHEMA,
            "migration_id": values.get("migration_id", ""),
            "operator": self.operator,
            "updated_at": _now_iso(),
            **values,
        }

    def _write_manifest(self, **values: Any) -> dict[str, Any]:
        payload = self._manifest(**values)
        _write_json(self.manifest_path, payload)
        return payload

    def _lock(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+")
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return handle

    @staticmethod
    def _verified_head_hash(result: dict[str, Any]) -> str | None:
        """Read the signed head from common verifier result shapes."""
        for key in ("tip_hash", "anchored_tip", "head_hash"):
            value = result.get(key)
            if isinstance(value, str):
                return value
        return None

    def run(self) -> dict[str, Any]:
        self.export_dir.mkdir(parents=True, exist_ok=True)
        migration_id = uuid.uuid4().hex
        lock = self._lock()
        began = False
        v2_started = False
        try:
            # The lock covers the complete state transition, including the
            # existing-segment check.  Checking before taking it allowed two
            # operators to both observe an empty segment and race activation.
            if self.v2_path.exists() and self.v2_path.stat().st_size:
                prior_manifest = self._read_manifest()
                initial_prev_hash = prior_manifest.get("v1_head_hash")
                if not isinstance(initial_prev_hash, str):
                    raise MigrationBlocked("existing v2 segment has no recorded v1 head")
                try:
                    result = verify_jsonl(self.v2_path, initial_prev_hash=initial_prev_hash)
                except (OSError, ValueError, TypeError, KeyError) as exc:
                    raise MigrationBlocked(f"existing v2 segment is unreadable: {exc}") from exc
                if not result.get("valid"):
                    raise MigrationBlocked("existing v2 segment is not independently verifiable")
                if self.chain._get_meta("migration_state") != "V2_ACTIVE":
                    raise MigrationBlocked("existing v2 segment requires operator review; chain is not V2_ACTIVE")
                return {"status": "already_active", "v2": result, "manifest": prior_manifest}

            internal = self.chain.verify_chain()
            if not internal.get("valid"):
                raise MigrationBlocked(f"v1 chain is not valid: {internal.get('reason')}")
            records = self.chain.get_chain()
            if not records:
                raise MigrationBlocked("v1 chain is empty; refusing to invent a migration head")
            v1_head_seq = records[-1].seq
            v1_head_hash = records[-1].record_hash

            self.chain.begin_migration(migration_id)
            began = True
            self._write_manifest(
                migration_id=migration_id,
                state="MIGRATING",
                v1_head_seq=v1_head_seq,
                v1_head_hash=v1_head_hash,
            )

            export_path = self.export_dir / "v1-export.jsonl"
            export_manifest_path = self.export_dir / "v1-export-manifest.json"
            try:
                exported = export_v1(self.chain.db_path, export_path, export_manifest_path)
            except ExportError as exc:
                raise MigrationBlocked(f"v1 export failed: {exc}") from exc
            if exported.manifest["head_hash"] != v1_head_hash:
                raise MigrationBlocked("exported v1 head does not match live v1 head")

            head_result = self.head_verifier(self.chain)
            if not isinstance(head_result, dict) or head_result.get("valid") is not True:
                raise MigrationBlocked(f"external v1 head verification failed: {head_result}")
            verified_head = self._verified_head_hash(head_result)
            if verified_head is not None and verified_head != v1_head_hash:
                raise MigrationBlocked("external verifier returned a different v1 head")
            verified_seq = head_result.get("anchored_seq")
            if verified_seq is not None and verified_seq != v1_head_seq:
                raise MigrationBlocked("external verifier returned a different v1 sequence")

            v2 = V2Segment(self.v2_path, v1_head_hash)
            v2_started = True
            first = v2.append(
                component="migration",
                event_type="migration.v2_started",
                payload={
                    "migration_id": migration_id,
                    "v1_head_seq": v1_head_seq,
                    "v1_head_hash": v1_head_hash,
                    "v1_export_sha256": exported.manifest["output_sha256"],
                },
                actor=self.operator,
                action="migration.start",
                authority={"decision_id": migration_id, "policy_version": "audit-migration-v1", "scope": ["audit-chain"]},
                execution={"executor_id": "migration-barrier", "result": "success"},
                verification={"verifier_id": "migration-barrier", "result": "verified", "method": "v1-export-and-external-head"},
                environment_ref="migration-test-runtime",
            )
            v2_result = v2.verify()
            if not v2_result.get("valid"):
                raise MigrationBlocked(f"v2 verification failed: {v2_result}")
            self.chain.complete_migration()
            began = False
            manifest = self._write_manifest(
                migration_id=migration_id,
                state="V2_ACTIVE",
                v1_head_seq=v1_head_seq,
                v1_head_hash=v1_head_hash,
                v1_export_manifest=str(export_manifest_path.resolve()),
                v2_path=str(self.v2_path.resolve()),
                v2_first_seq=first["seq"],
                v2_head_hash=first["record_hash"],
                v2_verification=v2_result,
                external_head_verification=head_result,
            )
            return {"status": "V2_ACTIVE", "manifest": manifest, "v2": v2_result}
        except Exception as exc:
            if began:
                if v2_started:
                    self.chain.block_migration(str(exc))
                else:
                    self.chain.abort_migration()
            self._write_manifest(
                migration_id=migration_id,
                state="MIGRATION_BLOCKED",
                reason=str(exc),
            )
            if isinstance(exc, MigrationBlocked):
                raise
            raise MigrationBlocked(str(exc)) from exc
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
            lock.close()

    def _read_manifest(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            return {}
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))
