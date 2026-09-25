from __future__ import annotations

import json
from pathlib import Path

import pytest

from msb_ledger.audit_v2 import (
    CanonicalizationError,
    VerificationError,
    canonicalize,
    compute_record_hash,
    loads_strict,
    verify_jsonl,
)

ROOT = Path(__file__).resolve().parents[2]
VECTORS = ROOT / "tests" / "fixtures" / "audit_golden_vectors.json"


def _vectors() -> dict:
    return json.loads(VECTORS.read_text(encoding="utf-8"))


def test_canonicalize_is_compact_sorted_and_utf8() -> None:
    value = {"b": 2, "a": "café ☕", "nested": {"z": True, "a": 1}}
    assert canonicalize(value) == '{"a":"café ☕","b":2,"nested":{"a":1,"z":true}}'


def test_canonicalize_rejects_non_finite_numbers() -> None:
    with pytest.raises(CanonicalizationError):
        canonicalize({"bad": float("nan")})


def test_strict_json_rejects_duplicate_keys() -> None:
    with pytest.raises(VerificationError, match="duplicate JSON object key"):
        loads_strict('{"a":1,"a":2}')


def test_first_golden_vector_matches_canonical_hash() -> None:
    vector = _vectors()["vectors"][0]
    record = vector["record_without_record_hash"]
    assert canonicalize(record) == vector["expected_canonical"]
    assert compute_record_hash({**record, "record_hash": "0" * 64}) == vector["expected_record_hash"]


def test_second_golden_vector_matches_canonical_hash() -> None:
    vector = _vectors()["vectors"][1]
    record = vector["record_without_record_hash"]
    assert canonicalize(record) == vector["expected_canonical"]
    assert compute_record_hash({**record, "record_hash": "0" * 64}) == vector["expected_record_hash"]


def test_standalone_verifier_rejects_gap(tmp_path: Path) -> None:
    record = _vectors()["vectors"][0]["record_without_record_hash"]
    record_hash = compute_record_hash({**record, "record_hash": "0" * 64})
    path = tmp_path / "chain.jsonl"
    path.write_text(
        json.dumps({**record, "record_hash": record_hash})
        + "\n"
        + json.dumps({**record, "seq": 3, "record_hash": "1" * 64})
        + "\n",
        encoding="utf-8",
    )
    result = verify_jsonl(path)
    assert result["valid"] is False
    assert "sequence gap" in result["reason"]
