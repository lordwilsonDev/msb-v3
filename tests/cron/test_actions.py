"""Tests for the built-in cron actions (cron/actions.py)."""

from __future__ import annotations

import gzip

import pytest

from msb_v3.cron import actions


def test_unknown_action_fails_closed() -> None:
    result = actions.run_action("nope", {})
    assert result["ok"] is False
    assert "unknown cron action" in result["summary"]


def test_action_exception_becomes_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(params):
        raise RuntimeError("kaboom")

    monkeypatch.setitem(actions.ACTIONS, "boom", boom)
    result = actions.run_action("boom", {})
    assert result["ok"] is False
    assert "kaboom" in result["summary"]


def test_health_check_reports(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    # DB healthy, chain db present + readable, kill switch readable.
    monkeypatch.setattr(actions.settings, "db_path", str(tmp_path / "msb.db"))
    chain = tmp_path / "uac" / "audit_chain.db"
    chain.parent.mkdir(parents=True)
    import sqlite3

    with sqlite3.connect(str(chain)) as conn:
        conn.execute("CREATE TABLE audit_records (id INTEGER)")
    monkeypatch.setattr(
        "msb_v3.governance.killswitch.KillSwitch",
        lambda: type("KS", (), {"is_armed": lambda self: False})(),
    )
    result = actions.run_action("health_check", {})
    assert result["ok"] is True
    assert result["detail"]["checks"]["db"] == "ok"


def test_health_check_fails_on_unreadable_chain(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(actions.settings, "db_path", str(tmp_path / "msb.db"))
    # No chain db at all -> degraded, not pretend-healthy.
    monkeypatch.setattr(
        "msb_v3.governance.killswitch.KillSwitch",
        lambda: type("KS", (), {"is_armed": lambda self: False})(),
    )
    result = actions.run_action("health_check", {})
    assert result["ok"] is False
    assert "audit_chain" in result["detail"]["checks"]


def test_audit_chain_verify_missing_db(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(actions.settings, "db_path", str(tmp_path / "msb.db"))
    result = actions.run_action("audit_chain_verify", {})
    assert result["ok"] is False
    assert "missing" in result["summary"]


def test_metric_export_writes_file(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    out = tmp_path / "metrics.txt"
    result = actions.run_action("metric_export", {"destination": str(out), "json": False})
    assert result["ok"] is True
    assert out.exists()
    assert "# HELP" in out.read_text() or "msb_v3" in out.read_text()


def test_http_call_localhost_allowed(tmp_path) -> None:
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = b'{"ok": true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:  # silence the test server
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        result = actions.run_action("http_call", {"url": f"http://127.0.0.1:{port}/health"})
        assert result["ok"] is True
        assert result["detail"]["status_code"] == 200
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_http_call_nonlocal_refused() -> None:
    result = actions.run_action("http_call", {"url": "http://example.com/health"})
    assert result["ok"] is False
    assert "allowlist" in result["summary"]


def test_http_call_bad_url_refused() -> None:
    result = actions.run_action("http_call", {"url": "file:///etc/passwd"})
    assert result["ok"] is False


def test_log_rotation_rotates_big_audit(tmp_path) -> None:
    audit = tmp_path / "audit.jsonl"
    audit.write_text("x" * 100)  # small file — under max_bytes
    # Force rotation with a tiny max_bytes.
    result = actions.run_action(
        "log_rotation",
        {"log_dir": str(tmp_path), "max_bytes": 10, "max_age_days": 365, "keep_days": 30},
    )
    assert result["ok"] is True
    archives = list((tmp_path / "archive").glob("audit-*.jsonl.gz"))
    assert len(archives) == 1
    with gzip.open(archives[0], "rt") as fh:
        assert fh.read() == "x" * 100
    # The live file is gone (rotated); next appends recreate it fresh.
    assert not audit.exists()


def test_log_rotation_snapshot_stale_logs(tmp_path) -> None:
    import os
    from datetime import datetime, timedelta, timezone

    log = tmp_path / "gateway.out.log"
    log.write_text("old stuff")
    old = datetime.now(timezone.utc) - timedelta(days=30)
    os.utime(log, (old.timestamp(), old.timestamp()))
    result = actions.run_action(
        "log_rotation",
        {"log_dir": str(tmp_path), "max_bytes": 10**9, "max_age_days": 7, "keep_days": 30},
    )
    assert result["ok"] is True
    # Snapshot archived, live file untouched (external processes may hold it).
    snaps = list((tmp_path / "archive").glob("gateway.out-*.log.gz"))
    assert len(snaps) == 1
    assert log.exists()
    assert log.read_text() == "old stuff"


def test_log_rotation_prunes_old_archives(tmp_path) -> None:
    import os
    from datetime import datetime, timedelta, timezone

    archive = tmp_path / "archive"
    archive.mkdir()
    stale = archive / "audit-old.jsonl.gz"
    stale.write_text("x")
    old = datetime.now(timezone.utc) - timedelta(days=60)
    os.utime(stale, (old.timestamp(), old.timestamp()))
    result = actions.run_action(
        "log_rotation",
        {"log_dir": str(tmp_path), "max_bytes": 10**9, "max_age_days": 365, "keep_days": 30},
    )
    assert result["ok"] is True
    assert not stale.exists()


def test_backup_spine_runs(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Point the backup at a scratch area with a real SQLite db to exercise the
    # online-backup + prune path without touching the deployment.
    import sqlite3

    data = tmp_path / "data"
    data.mkdir()
    with sqlite3.connect(str(data / "msb_v3.db")) as conn:
        conn.execute("CREATE TABLE t (id INTEGER)")
    monkeypatch.setattr(
        "msb_v3.ops.backup.default_paths",
        lambda: (data, tmp_path / "storage", tmp_path / "backups"),
    )
    result = actions.run_action("backup_spine", {"keep": 3})
    assert result["ok"] is True
    assert result["detail"]["db_count"] == 1
    backups = sorted((tmp_path / "backups").iterdir())
    assert len(backups) == 1
