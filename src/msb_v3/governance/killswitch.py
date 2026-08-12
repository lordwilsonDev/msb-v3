"""KillSwitch — one control to pause the whole loop.

Persisted in SQLite so the state survives restarts (a restart must never
silently clear the kill switch). Fail-closed: if the state cannot be read,
the switch is treated as ARMED — the loop stops rather than runs on
without governance.

Every arm/disarm is written to the UAC audit chain (killswitch.armed /
killswitch.disarmed) so the loop's pause history is never a black box.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from msb_v3.governance.db import default_db_path
from msb_v3.uac.audit_chain import AuditChain


class GovernanceHalt(Exception):
    """Raised by ``require_allowed()`` when the brakes refuse work."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class KillSwitch:
    def __init__(self, db_path: Optional[str] = None, audit_chain: Optional[AuditChain] = None) -> None:
        self.db_path = str(default_db_path() if db_path is None else db_path)
        self._audit = audit_chain if audit_chain is not None else AuditChain()
        self._init_db()

    def _init_db(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS kill_switch_state ("
                " id INTEGER PRIMARY KEY CHECK (id = 1),"
                " armed INTEGER NOT NULL,"
                " armed_at TEXT,"
                " armed_by TEXT,"
                " reason TEXT)"
            )
            conn.execute(
                "INSERT OR IGNORE INTO kill_switch_state(id, armed) VALUES (1, 0)"
            )

    def _audit_append(self, event_type: str, payload: dict) -> Optional[str]:
        """Audit an arm/disarm; on failure return the error instead of dropping
        it silently (the state change is already committed — the audit chain is
        a separate DB, so the failure must be surfaced, not hidden)."""
        try:
            self._audit.append("killswitch", event_type, payload)
            return None
        except Exception as exc:
            return str(exc)

    def arm(self, operator: str, reason: str = "") -> dict:
        now = _now_iso()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE kill_switch_state SET armed=1, armed_at=?, armed_by=?, reason=? WHERE id=1",
                (now, operator, reason),
            )
        state = self.state()
        audit_failed = self._audit_append("armed", {"operator": operator, "reason": reason})
        if audit_failed:
            state["audit_failed"] = audit_failed
        return state

    def disarm(self, operator: str) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE kill_switch_state SET armed=0, armed_at=NULL, armed_by=NULL, reason=NULL WHERE id=1"
            )
        state = self.state()
        audit_failed = self._audit_append("disarmed", {"operator": operator})
        if audit_failed:
            state["audit_failed"] = audit_failed
        return state

    def state(self) -> dict:
        """Current state; on read failure returns armed=True (fail-closed)."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT armed, armed_at, armed_by, reason FROM kill_switch_state WHERE id=1"
                ).fetchone()
            if row is None:
                return {"armed": True, "fail_closed": True, "reason": "no state row"}
            return {
                "armed": bool(row["armed"]),
                "armed_at": row["armed_at"],
                "armed_by": row["armed_by"],
                "reason": row["reason"],
            }
        except Exception as exc:  # unreadable state => treat as armed
            return {"armed": True, "fail_closed": True, "reason": str(exc)}

    def is_armed(self) -> bool:
        return self.state()["armed"]

    def require_allowed(self) -> None:
        """Imperative form: raises GovernanceHalt when the switch is armed."""
        if self.is_armed():
            raise GovernanceHalt("kill switch armed — loop paused")
