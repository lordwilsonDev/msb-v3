from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from msb_v3.node.identity import IdentityStore, NodeAuthError


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _insert_device(conn: sqlite3.Connection, device_id: str, status: str = "ACTIVE") -> None:
    conn.execute(
        "INSERT INTO node_devices(device_id, public_key, hardware_assurance, status, created_at, last_seen_at)"
        " VALUES(?,?,?,?,?,?)",
        (device_id, "uncompressed-placeholder", "software", status, _now_iso(), _now_iso()),
    )


def _insert_session(
    conn: sqlite3.Connection,
    session_id: str,
    device_id: str,
    expires_at: str,
    status: str = "ACTIVE",
) -> None:
    conn.execute(
        "INSERT INTO node_sessions(session_id, device_id, issued_at, expires_at, status) VALUES(?,?,?,?,?)",
        (session_id, device_id, _now_iso(), expires_at, status),
    )


def test_prune_removes_stale_replays_challenges_and_sessions(tmp_path: Path) -> None:
    store = IdentityStore(str(tmp_path / "node.db"), "pairing", clock_skew_s=60)
    now = datetime.now(timezone.utc)
    old = (now - timedelta(seconds=300)).isoformat()
    expired = (now - timedelta(seconds=10)).isoformat()
    future = (now + timedelta(seconds=300)).isoformat()

    with sqlite3.connect(store.db_path) as conn:
        _insert_device(conn, "dev-active")
        _insert_device(conn, "dev-revoked", status="REVOKED")
        _insert_session(conn, "sess-live", "dev-active", future)
        _insert_session(conn, "sess-expired", "dev-active", expired)
        _insert_session(conn, "sess-revoked", "dev-revoked", future, status="REVOKED")
        for request_id, session_id, nonce in (
            ("r-live", "sess-live", "n-live"),
            ("r-expired", "sess-expired", "n-expired"),
            ("r-revoked", "sess-revoked", "n-revoked"),
        ):
            conn.execute(
                "INSERT INTO node_replays(request_id, session_id, nonce, seen_at) VALUES(?,?,?,?)",
                (request_id, session_id, nonce, _now_iso()),
            )
        for challenge, device_id, created_at, used in (
            ("c-fresh", "dev-active", _now_iso(), 0),
            ("c-old", "dev-active", old, 0),
            ("c-used", "dev-active", _now_iso(), 1),
            ("c-revoked", "dev-revoked", _now_iso(), 0),
        ):
            conn.execute(
                "INSERT INTO node_challenges(challenge, device_id, created_at, used) VALUES(?,?,?,?)",
                (challenge, device_id, created_at, used),
            )

    store.prune()

    with sqlite3.connect(store.db_path) as conn:
        assert {row[0] for row in conn.execute("SELECT request_id FROM node_replays")} == {"r-live"}
        assert {row[0] for row in conn.execute("SELECT challenge FROM node_challenges")} == {"c-fresh"}
        assert {row[0] for row in conn.execute("SELECT session_id FROM node_sessions")} == {"sess-live"}


def test_request_path_prunes_before_validation(tmp_path: Path) -> None:
    store = IdentityStore(str(tmp_path / "node.db"), "pairing", clock_skew_s=60)
    now = datetime.now(timezone.utc)
    expired = (now - timedelta(seconds=10)).isoformat()
    with sqlite3.connect(store.db_path) as conn:
        _insert_device(conn, "dev-active")
        _insert_session(conn, "sess-expired", "dev-active", expired)
        conn.execute(
            "INSERT INTO node_replays(request_id, session_id, nonce, seen_at) VALUES(?,?,?,?)",
            ("r-stale", "sess-expired", "n-stale", _now_iso()),
        )

    # Malformed envelope: validation fails, but prune must already have run.
    with pytest.raises(NodeAuthError, match="malformed"):
        store.verify_request({"request_id": "", "session_id": "", "timestamp": "", "nonce": "", "intent": {}, "signature": ""})

    with sqlite3.connect(store.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM node_replays").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM node_sessions").fetchone()[0] == 0
