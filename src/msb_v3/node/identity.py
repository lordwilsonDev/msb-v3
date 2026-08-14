"""Durable identity, session, and replay state for the Sovereign Node."""

from __future__ import annotations

import hmac
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict

from msb_v3.node.crypto import verify
from msb_v3.node.protocol import (
    b64decode,
    canonical_json,
    request_signature_payload,
    session_signature_payload,
)


class NodeAuthError(ValueError):
    pass


class ReplayError(NodeAuthError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise NodeAuthError("invalid timestamp") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class IdentityStore:
    def __init__(self, db_path: str, pairing_code: str, session_ttl_s: int = 900, clock_skew_s: int = 60) -> None:
        self.db_path = str(db_path)
        self.pairing_code = pairing_code
        self.session_ttl_s = session_ttl_s
        self.clock_skew_s = clock_skew_s
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS node_devices (
                    device_id TEXT PRIMARY KEY,
                    public_key TEXT NOT NULL,
                    hardware_assurance TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    last_seen_at TEXT
                );
                CREATE TABLE IF NOT EXISTS node_challenges (
                    challenge TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    used INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS node_sessions (
                    session_id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    issued_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    status TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS node_replays (
                    request_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    nonce TEXT NOT NULL UNIQUE,
                    seen_at TEXT NOT NULL
                );
                """
            )

    def enroll(self, device_id: str, public_key: str, pairing_code: str, hardware_assurance: str) -> Dict[str, str]:
        if not self.pairing_code or not hmac.compare_digest(pairing_code, self.pairing_code):
            raise NodeAuthError("invalid pairing code")
        try:
            key = b64decode(public_key)
        except Exception as exc:
            raise NodeAuthError("invalid public key encoding") from exc
        if len(key) != 65 or key[0] != 4:
            raise NodeAuthError("public key must be an uncompressed P-256 key")
        now = _now().isoformat()
        with self._connect() as conn:
            existing = conn.execute("SELECT status, public_key FROM node_devices WHERE device_id=?", (device_id,)).fetchone()
            if existing and existing["status"] == "REVOKED":
                raise NodeAuthError("device is revoked")
            if existing and existing["public_key"] != public_key:
                raise NodeAuthError("device already enrolled with a different key")
            conn.execute(
                "INSERT INTO node_devices(device_id, public_key, hardware_assurance, status, created_at, last_seen_at)"
                " VALUES(?,?,?,?,?,?) ON CONFLICT(device_id) DO UPDATE SET status='ACTIVE', last_seen_at=excluded.last_seen_at",
                (device_id, public_key, hardware_assurance, "ACTIVE", now, now),
            )
        return {"device_id": device_id, "status": "ACTIVE", "hardware_assurance": hardware_assurance}

    def prune(self) -> None:
        """Bound the anti-replay tables to what can still be valid.

        Removes: replay records whose session is no longer ACTIVE or has
        expired, consumed or stale challenges, and expired/revoked sessions.
        All timestamps are written via ``_now().isoformat()`` (UTC), so the
        lexicographic comparisons below are exact for this controlled format.
        """
        now = _now()
        challenge_cutoff = (now - timedelta(seconds=self.clock_skew_s * 2)).isoformat()
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM node_replays WHERE session_id IN ("
                "  SELECT session_id FROM node_sessions WHERE status != 'ACTIVE' OR expires_at < ?"
                ")",
                (now.isoformat(),),
            )
            conn.execute(
                "DELETE FROM node_challenges WHERE used = 1 OR created_at < ? OR device_id NOT IN ("
                "  SELECT device_id FROM node_devices WHERE status = 'ACTIVE'"
                ")",
                (challenge_cutoff,),
            )
            conn.execute(
                "DELETE FROM node_sessions WHERE status != 'ACTIVE' OR expires_at < ?",
                (now.isoformat(),),
            )

    def challenge(self, device_id: str) -> str:
        self.prune()
        with self._connect() as conn:
            row = conn.execute("SELECT status FROM node_devices WHERE device_id=?", (device_id,)).fetchone()
            if not row or row["status"] != "ACTIVE":
                raise NodeAuthError("device is not active")
            value = secrets.token_urlsafe(32)
            conn.execute(
                "INSERT INTO node_challenges(challenge, device_id, created_at) VALUES(?,?,?)",
                (value, device_id, _now().isoformat()),
            )
        return value

    def open_session(self, device_id: str, challenge: str, signature: str) -> Dict[str, str]:
        self.prune()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT c.created_at, c.used, d.public_key, d.status FROM node_challenges c"
                " JOIN node_devices d ON d.device_id=c.device_id WHERE c.challenge=? AND c.device_id=?",
                (challenge, device_id),
            ).fetchone()
            if not row or row["status"] != "ACTIVE":
                raise NodeAuthError("unknown or inactive challenge")
            if row["used"]:
                raise ReplayError("challenge already used")
            if _now() - _parse_time(row["created_at"]) > timedelta(seconds=self.clock_skew_s * 2):
                raise NodeAuthError("challenge expired")
            try:
                valid = verify(
                    b64decode(row["public_key"]),
                    canonical_json(session_signature_payload(device_id, challenge)),
                    b64decode(signature),
                )
            except Exception as exc:
                raise NodeAuthError("invalid session signature") from exc
            if not valid:
                raise NodeAuthError("invalid session signature")
            issued = _now()
            expires = issued + timedelta(seconds=self.session_ttl_s)
            session_id = secrets.token_urlsafe(24)
            conn.execute("UPDATE node_challenges SET used=1 WHERE challenge=?", (challenge,))
            conn.execute(
                "INSERT INTO node_sessions(session_id, device_id, issued_at, expires_at, status) VALUES(?,?,?,?,?)",
                (session_id, device_id, issued.isoformat(), expires.isoformat(), "ACTIVE"),
            )
        return {"session_id": session_id, "device_id": device_id, "expires_at": expires.isoformat()}

    def verify_request(self, envelope: Dict[str, object]) -> str:
        self.prune()
        request_id = str(envelope.get("request_id", ""))
        session_id = str(envelope.get("session_id", ""))
        timestamp = str(envelope.get("timestamp", ""))
        nonce = str(envelope.get("nonce", ""))
        intent = envelope.get("intent")
        signature = str(envelope.get("signature", ""))
        if not request_id or not session_id or not timestamp or not nonce or not isinstance(intent, dict):
            raise NodeAuthError("malformed signed request")
        request_time = _parse_time(timestamp)
        if abs((_now() - request_time).total_seconds()) > self.clock_skew_s:
            raise NodeAuthError("request timestamp outside allowed clock skew")
        with self._connect() as conn:
            row = conn.execute(
                "SELECT s.device_id, s.expires_at, s.status, d.public_key, d.status AS device_status"
                " FROM node_sessions s JOIN node_devices d ON d.device_id=s.device_id"
                " WHERE s.session_id=?",
                (session_id,),
            ).fetchone()
            if not row or row["status"] != "ACTIVE" or row["device_status"] != "ACTIVE":
                raise NodeAuthError("session or device is inactive")
            if _now() >= _parse_time(row["expires_at"]):
                conn.execute("UPDATE node_sessions SET status='EXPIRED' WHERE session_id=?", (session_id,))
                raise NodeAuthError("session expired")
            if conn.execute("SELECT 1 FROM node_replays WHERE request_id=? OR nonce=?", (request_id, nonce)).fetchone():
                raise ReplayError("request or nonce already used")
            try:
                valid = verify(
                    b64decode(row["public_key"]),
                    canonical_json(request_signature_payload(request_id, session_id, timestamp, nonce, intent)),
                    b64decode(signature),
                )
            except Exception as exc:
                raise NodeAuthError("invalid request signature") from exc
            if not valid:
                raise NodeAuthError("invalid request signature")
            try:
                conn.execute(
                    "INSERT INTO node_replays(request_id, session_id, nonce, seen_at) VALUES(?,?,?,?)",
                    (request_id, session_id, nonce, _now().isoformat()),
                )
            except sqlite3.IntegrityError as exc:
                raise ReplayError("request or nonce already used") from exc
            conn.execute("UPDATE node_devices SET last_seen_at=? WHERE device_id=?", (_now().isoformat(), row["device_id"]))
        return str(row["device_id"])

    def revoke(self, device_id: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE node_devices SET status='REVOKED' WHERE device_id=?", (device_id,))
            conn.execute("UPDATE node_sessions SET status='REVOKED' WHERE device_id=?", (device_id,))

    def status(self) -> Dict[str, object]:
        with self._connect() as conn:
            devices = conn.execute("SELECT device_id, status, hardware_assurance, last_seen_at FROM node_devices").fetchall()
            active_sessions = conn.execute("SELECT count(*) AS n FROM node_sessions WHERE status='ACTIVE'").fetchone()["n"]
        return {"devices": [dict(row) for row in devices], "active_sessions": active_sessions}
