"""Tests for Triumvirate Phase 4 — Argus self-annealing audits."""
from __future__ import annotations

import sqlite3

from msb_v3.observability.audit import ArgusAuditor, MulchFinding


def _write_temp(root, name, content):
    path = root / name
    path.write_text(content)
    return path


def test_record_mulch_writes_sqlite(tmp_path, monkeypatch):
    import msb_v3.observability.audit as arg_mod
    monkeypatch.setattr(arg_mod, "_RUNTIME_ROOT", tmp_path / "triumvirate")
    monkeypatch.setattr(arg_mod, "_MULCH_DB", tmp_path / "triumvirate" / "mulch_learnings.db")
    auditor = ArgusAuditor()
    finding = MulchFinding(component="test", finding_type="f1", description="d1")
    result = auditor.record_mulch(finding)
    assert result["id"] == 1
    with sqlite3.connect(tmp_path / "triumvirate" / "mulch_learnings.db") as conn:
        rows = conn.execute("SELECT component, finding_type FROM mulch_learnings").fetchall()
    assert rows == [("test", "f1")]


def test_audit_directives_detects_fixme(tmp_path, monkeypatch):
    import msb_v3.observability.audit as arg_mod
    monkeypatch.setattr(arg_mod, "_RUNTIME_ROOT", tmp_path / "triumvirate")
    monkeypatch.setattr(arg_mod, "_MULCH_DB", tmp_path / "triumvirate" / "mulch_learnings.db")
    root = tmp_path / "directives"
    root.mkdir()
    _write_temp(root, "policy.md", "FIXME: review this")
    auditor = ArgusAuditor()
    findings = auditor.audit_directives(str(root))
    assert len(findings) == 1
    assert findings[0]["finding_type"] == "outdated_rule"


def test_audit_memory_detects_orphan(tmp_path, monkeypatch):
    import msb_v3.observability.audit as arg_mod
    monkeypatch.setattr(arg_mod, "_RUNTIME_ROOT", tmp_path / "triumvirate")
    monkeypatch.setattr(arg_mod, "_MULCH_DB", tmp_path / "triumvirate" / "mulch_learnings.db")
    root = tmp_path
    _write_temp(root, "memory.mmd", "contains orphan node")
    auditor = ArgusAuditor()
    findings = auditor.audit_memory(str(root / "memory.mmd"))
    assert len(findings) == 1
    assert findings[0]["finding_type"] == "orphan_node"


def test_audit_soul_detects_drift(tmp_path, monkeypatch):
    import msb_v3.observability.audit as arg_mod
    monkeypatch.setattr(arg_mod, "_RUNTIME_ROOT", tmp_path / "triumvirate")
    monkeypatch.setattr(arg_mod, "_MULCH_DB", tmp_path / "triumvirate" / "mulch_learnings.db")
    root = tmp_path
    _write_temp(root, "soul.md", "drift detected")
    auditor = ArgusAuditor()
    findings = auditor.audit_soul(str(root / "soul.md"))
    assert len(findings) == 1
    assert findings[0]["finding_type"] == "drift"


def test_audit_run_logs_detects_error(tmp_path, monkeypatch):
    import msb_v3.observability.audit as arg_mod
    monkeypatch.setattr(arg_mod, "_RUNTIME_ROOT", tmp_path / "triumvirate")
    monkeypatch.setattr(arg_mod, "_MULCH_DB", tmp_path / "triumvirate" / "mulch_learnings.db")
    log_root = tmp_path / "logs"
    log_root.mkdir()
    _write_temp(log_root, "run.log", "line1\nERROR\nline2")
    auditor = ArgusAuditor()
    findings = auditor.audit_run_logs(str(log_root))
    assert len(findings) == 1
    assert findings[0]["finding_type"] == "error_pattern"


def test_full_run_returns_counts(tmp_path, monkeypatch):
    import msb_v3.observability.audit as arg_mod
    monkeypatch.setattr(arg_mod, "_RUNTIME_ROOT", tmp_path / "triumvirate")
    monkeypatch.setattr(arg_mod, "_MULCH_DB", tmp_path / "triumvirate" / "mulch_learnings.db")
    root = tmp_path
    directives_dir = root / "directives"
    directives_dir.mkdir()
    _write_temp(directives_dir, "policy.md", "FIXME: review this")
    _write_temp(root, "memory.mmd", "orphan")
    _write_temp(root, "soul.md", "drift")
    log_root = root / "logs"
    log_root.mkdir()
    _write_temp(log_root, "run.log", "line1\nERROR\nline2")
    auditor = ArgusAuditor()
    result = auditor.run(
        directives_dir=str(directives_dir),
        memory_file=str(root / "memory.mmd"),
        soul_file=str(root / "soul.md"),
        logs_dir=str(log_root),
    )
    assert "findings" in result
    assert result["count"] >= 1
