"""Canonical serialization and standalone verification for ``msb.audit.v2``.

This module is intentionally independent of :mod:`msb_ledger.audit_chain`.
It accepts already-exported JSONL records, recomputes their hashes, and verifies
sequence continuity.  Keeping the verifier free of the SQLite producer makes
it suitable for a second process or replacement host.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "msb.audit.v2"
GENESIS_HASH = "0" * 64
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_FIELDS = {
    "schema_version",
    "record_type",
    "seq",
    "recorded_at",
    "component",
    "actor",
    "action",
    "authority",
    "execution",
    "verification",
    "payload",
    "environment_ref",
    "prev_hash",
    "record_hash",
}


class CanonicalizationError(ValueError):
    """The value cannot be represented by the audit canonical format."""


class VerificationError(ValueError):
    """A record is structurally or cryptographically invalid."""


def _validate_value(value: Any, path: str = "record") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CanonicalizationError(f"{path} contains a non-finite number")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_value(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalizationError(f"{path} has a non-string object key")
            _validate_value(item, f"{path}.{key}")
        return
    raise CanonicalizationError(f"{path} has unsupported type {type(value).__name__}")


def canonicalize(value: Any) -> str:
    """Return the deterministic v2 JSON representation.

    This is the supported Python profile of the v2 canonical format.  It is
    intentionally small and dependency-free: UTF-8 strings, sorted object keys,
    compact separators, no non-finite numbers, and no trailing newline.
    """
    _validate_value(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def canonical_bytes(value: Any) -> bytes:
    """Return UTF-8 canonical bytes for a JSON-compatible value."""
    return canonicalize(value).encode("utf-8")


def sha256_hex(value: Any) -> str:
    """Return the lowercase SHA-256 digest of canonical value bytes."""
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def record_preimage(record: dict[str, Any]) -> dict[str, Any]:
    """Return the record fields covered by ``record_hash``."""
    if not isinstance(record, dict):
        raise VerificationError("record must be a JSON object")
    missing = _REQUIRED_FIELDS - record.keys()
    extra = record.keys() - _REQUIRED_FIELDS
    if missing:
        raise VerificationError(f"record is missing required fields: {sorted(missing)}")
    if extra:
        raise VerificationError(f"record has unsupported fields: {sorted(extra)}")
    if record["schema_version"] != SCHEMA_VERSION:
        raise VerificationError(f"unsupported schema_version: {record['schema_version']!r}")
    if not isinstance(record["seq"], int) or isinstance(record["seq"], bool) or record["seq"] < 1:
        raise VerificationError("seq must be a positive integer")
    if not isinstance(record["prev_hash"], str) or not _HASH_RE.fullmatch(record["prev_hash"]):
        raise VerificationError("prev_hash must be 64 lowercase hexadecimal characters")
    if not isinstance(record["record_hash"], str) or not _HASH_RE.fullmatch(record["record_hash"]):
        raise VerificationError("record_hash must be 64 lowercase hexadecimal characters")
    for field in ("record_type", "recorded_at", "component", "actor", "action", "environment_ref"):
        if not isinstance(record[field], str) or not record[field].strip():
            raise VerificationError(f"{field} must be a non-empty string")
    for field in ("authority", "execution", "verification", "payload"):
        if not isinstance(record[field], dict):
            raise VerificationError(f"{field} must be a JSON object")
    return {key: value for key, value in record.items() if key != "record_hash"}


def compute_record_hash(record: dict[str, Any]) -> str:
    """Compute a v2 record hash after validating its preimage."""
    return sha256_hex(record_preimage(record))


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise VerificationError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def loads_strict(data: str) -> Any:
    """Parse JSON while rejecting duplicate object keys."""
    return json.loads(data, object_pairs_hook=_strict_pairs)


def iter_jsonl(path: str | Path) -> Iterable[dict[str, Any]]:
    """Yield JSON objects from a non-empty JSONL file."""
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                raise VerificationError(f"blank line in JSONL at line {line_number}")
            try:
                value = loads_strict(line)
            except json.JSONDecodeError as exc:
                raise VerificationError(f"invalid JSON at line {line_number}: {exc}") from exc
            if not isinstance(value, dict):
                raise VerificationError(f"JSONL line {line_number} is not an object")
            yield value


def verify_jsonl(
    path: str | Path,
    *,
    initial_prev_hash: str = GENESIS_HASH,
) -> dict[str, Any]:
    """Verify sequence, predecessor links, and record hashes in a v2 chain.

    ``initial_prev_hash`` lets a v2 segment link to a verified historical v1
    head while retaining the standalone verifier's zero-configuration default.
    """
    if not isinstance(initial_prev_hash, str) or not _HASH_RE.fullmatch(initial_prev_hash):
        return {
            "valid": False,
            "record_count": 0,
            "reason": "initial_prev_hash must be 64 lowercase hexadecimal characters",
        }
    expected_seq = 1
    expected_prev = initial_prev_hash
    count = 0
    head = GENESIS_HASH
    for record in iter_jsonl(path):
        try:
            preimage = record_preimage(record)
        except (CanonicalizationError, VerificationError) as exc:
            return {
                "valid": False,
                "record_count": count,
                "broken_at_seq": record.get("seq") if isinstance(record, dict) else None,
                "reason": str(exc),
            }
        seq = record["seq"]
        if seq != expected_seq:
            return {
                "valid": False,
                "record_count": count,
                "broken_at_seq": seq,
                "reason": f"sequence gap or reorder: expected {expected_seq}, got {seq}",
            }
        if record["prev_hash"] != expected_prev:
            return {
                "valid": False,
                "record_count": count,
                "broken_at_seq": seq,
                "reason": "prev_hash does not match preceding record",
            }
        try:
            recomputed = sha256_hex(preimage)
        except CanonicalizationError as exc:
            return {
                "valid": False,
                "record_count": count,
                "broken_at_seq": seq,
                "reason": str(exc),
            }
        if recomputed != record["record_hash"]:
            return {
                "valid": False,
                "record_count": count,
                "broken_at_seq": seq,
                "reason": "stored hash does not match canonical record hash",
            }
        expected_seq += 1
        expected_prev = record["record_hash"]
        head = record["record_hash"]
        count += 1
    return {
        "valid": True,
        "record_count": count,
        "head_hash": head,
        "head_seq": count,
    }


def _main() -> int:
    parser = argparse.ArgumentParser(description="Verify an independent msb.audit.v2 JSONL export")
    parser.add_argument("jsonl", type=Path)
    args = parser.parse_args()
    try:
        result = verify_jsonl(args.jsonl)
    except (OSError, VerificationError) as exc:
        result = {"valid": False, "record_count": 0, "reason": str(exc)}
    print(json.dumps(result, sort_keys=True))
    return 0 if result.get("valid") else 2


if __name__ == "__main__":
    raise SystemExit(_main())
