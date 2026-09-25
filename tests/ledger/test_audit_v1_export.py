from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from msb_ledger import audit_v1_export as exporter
from msb_ledger.audit_v1_export import ExportError, export_v1
from msb_v3.uac.audit_chain import AuditChain, tamper


def _chain(tmp_path: Path, count: int = 3) -> AuditChain:
    chain = AuditChain(str(tmp_path / "audit.db"), allow_keyless=True)
    for index in range(count):
        chain.append("test", "event", {"index": index, "text": "café"})
    return chain


def test_export_is_deterministic_and_manifest_describes_source(tmp_path: Path) -> None:
    db = _chain(tmp_path).db_path
    output = tmp_path / "export" / "v1.jsonl"
    manifest = tmp_path / "export" / "manifest.json"

    first = export_v1(db, output, manifest)
    first_bytes = output.read_bytes()
    second = export_v1(db, output, manifest)

    assert output.read_bytes() == first_bytes
    assert first.manifest["record_count"] == 3
    assert first.manifest["head_hash"] == second.manifest["head_hash"]
    assert first.manifest["source_db_sha256"] == second.manifest["source_db_sha256"]
    assert first.manifest["source_mutation_during_export"] is False
    records = [json.loads(line) for line in output.read_text().splitlines()]
    assert [record["seq"] for record in records] == [1, 2, 3]
    assert records[0]["schema_version"] == "msb.audit.v1-export"


def test_exporter_connection_is_read_only(tmp_path: Path) -> None:
    db = _chain(tmp_path, 1).db_path
    conn = exporter._open_read_only(db)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute("INSERT INTO audit_records(component) VALUES ('forbidden')")
    conn.close()


def test_export_refuses_source_mutation_before_writing_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _chain(tmp_path, 2).db_path
    output = tmp_path / "export.jsonl"
    manifest = tmp_path / "manifest.json"
    original = exporter._source_fingerprint
    calls = 0

    def changing_fingerprint(path: Path):
        nonlocal calls
        calls += 1
        result = dict(original(path))
        if calls == 2:
            main = result[path.name]
            assert main is not None
            result[path.name] = (main[0], main[1] + 1, main[2], "0" * 64)
        return result

    monkeypatch.setattr(exporter, "_source_fingerprint", changing_fingerprint)
    with pytest.raises(ExportError, match="source database changed"):
        export_v1(db, output, manifest)
    assert not output.exists()
    assert not manifest.exists()


def test_export_rejects_corrupt_payload_hash(tmp_path: Path) -> None:
    chain = _chain(tmp_path, 2)
    tamper(chain.db_path, "UPDATE audit_records SET payload=? WHERE seq=1", ('{"index":999}',))
    with pytest.raises(ExportError, match="record hash mismatch"):
        export_v1(chain.db_path, tmp_path / "out.jsonl", tmp_path / "manifest.json")


def test_export_rejects_sequence_gap(tmp_path: Path) -> None:
    chain = _chain(tmp_path, 3)
    tamper(chain.db_path, "DELETE FROM audit_records WHERE seq=2")
    with pytest.raises(ExportError, match="sequence gap"):
        export_v1(chain.db_path, tmp_path / "out.jsonl", tmp_path / "manifest.json")


def test_export_rejects_source_overwrite_targets(tmp_path: Path) -> None:
    db = _chain(tmp_path, 1).db_path
    with pytest.raises(ExportError, match="output must not overwrite"):
        export_v1(db, db, tmp_path / "manifest.json")
    with pytest.raises(ExportError, match="manifest must not overwrite"):
        export_v1(db, tmp_path / "out.jsonl", db)
    with pytest.raises(ExportError, match="different files"):
        export_v1(db, tmp_path / "same", tmp_path / "same")
