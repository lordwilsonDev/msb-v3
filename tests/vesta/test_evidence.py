from __future__ import annotations

from pathlib import Path

import pytest

from msb_v3.vesta.evidence import EvidenceError, EvidenceStore


def test_evidence_is_content_addressed_and_verified(tmp_path: Path) -> None:
    store = EvidenceStore(str(tmp_path / "objects"), str(tmp_path / "evidence.db"))
    first = store.record_json({"b": 2, "a": 1}, "fixture", {"task_id": "task-1"})
    second = store.record_json({"a": 1, "b": 2}, "fixture", {"task_id": "task-1"})

    assert first["evidence_id"] == second["evidence_id"]
    assert first["verified"] is True
    assert first["size_bytes"] > 0
    assert first["metadata"]["task_id"] == "task-1"


def test_evidence_corruption_is_reported_without_repair(tmp_path: Path) -> None:
    store = EvidenceStore(str(tmp_path / "objects"), str(tmp_path / "evidence.db"))
    evidence = store.record_bytes(b"original", "fixture")
    path = store.root / evidence["relative_path"]
    path.write_bytes(b"tampered")

    result = store.get(evidence["evidence_id"])
    assert result["verified"] is False
    assert result["sha256"] != ""  # the original hash remains the forensic reference

    path.unlink()
    assert store.get(evidence["evidence_id"])["verified"] is False


def test_unknown_evidence_fails_closed(tmp_path: Path) -> None:
    store = EvidenceStore(str(tmp_path / "objects"), str(tmp_path / "evidence.db"))
    with pytest.raises(EvidenceError, match="unknown evidence"):
        store.get("ev_missing")
