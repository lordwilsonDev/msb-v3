"""KillSwitch — the brakes, global and scoped.

One control to pause the whole loop, plus scoped lockdown (unified-
architecture §13, forensic finding 2026-08-15): ``STOP agent_07`` must not
necessarily mean ``STOP entire MSB``, and ``DISABLE shell_execute`` must not
disable ``vault_search``. Scope types: tenant / agent / task / tool /
capability / resource. A scoped arm blocks only matching callers; the global
arm still blocks everyone. ``is_blocked()`` = global OR matching scope, so
scopes never loosen a global lockdown.

Persisted in SQLite so the state survives restarts (a restart must never
silently clear the kill switch). Fail-closed: if the state cannot be read,
the switch is treated as ARMED — the loop stops rather than runs on
without governance.

Every arm/disarm is written to the UAC audit chain (killswitch.armed /
killswitch.disarmed / killswitch.scope_armed / killswitch.scope_disarmed)
so the loop's pause history is never a black box.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from msb_ledger.audit_chain import AuditChainLike
from msb_ledger.chain_anchor import anchored_chain_from_env
from msb_v3.governance.db import default_db_path


class GovernanceHalt(Exception):
    """Raised by ``require_allowed()`` when the brakes refuse work."""


# Allowed scope types (unified-architecture §13). Anything else is rejected
# at the API boundary so the scopes table can't accrete junk rows.
SCOPE_TYPES = frozenset({"tenant", "agent", "task", "tool", "capability", "resource"})


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class KillSwitch:
    def __init__(self, db_path: Optional[str] = None, audit_chain: Optional[AuditChainLike] = None) -> None:
        self.db_path = str(default_db_path() if db_path is None else db_path)
        self._audit = audit_chain if audit_chain is not None else anchored_chain_from_env()
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
                "CREATE TABLE IF NOT EXISTS kill_switch_scopes ("
                " scope_type TEXT NOT NULL,"
                " scope_id TEXT NOT NULL,"
                " armed INTEGER NOT NULL,"
                " armed_at TEXT,"
                " armed_by TEXT,"
                " reason TEXT,"
                " PRIMARY KEY (scope_type, scope_id))"
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
            base = {
                "armed": bool(row["armed"]),
                "armed_at": row["armed_at"],
                "armed_by": row["armed_by"],
                "reason": row["reason"],
            }
            base["scopes"] = self.list_scopes()
            return base
        except Exception as exc:  # unreadable state => treat as armed
            return {"armed": True, "fail_closed": True, "reason": str(exc)}

    def is_armed(self) -> bool:
        return self.state()["armed"]

    # --- scoped lockdown (unified-architecture §13) ----------------------

    def arm_scope(self, scope_type: str, scope_id: str, operator: str, reason: str = "") -> dict:
        """Arm the brakes for one scope only (e.g. agent_07, tool shell_execute).

        Raises ValueError for an unknown scope type; the global switch is not
        affected. Audit event: killswitch.scope_armed.
        """
        if scope_type not in SCOPE_TYPES:
            raise ValueError(f"unknown scope type {scope_type!r} (allowed: {sorted(SCOPE_TYPES)})")
        if not scope_id:
            raise ValueError("scope_id is required")
        now = _now_iso()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO kill_switch_scopes"
                " (scope_type, scope_id, armed, armed_at, armed_by, reason)"
                " VALUES (?,?,1,?,?,?)",
                (scope_type, scope_id, now, operator, reason),
            )
        state = self.scope_state(scope_type, scope_id)
        audit_failed = self._audit_append(
            "scope_armed", {"scope_type": scope_type, "scope_id": scope_id, "operator": operator, "reason": reason}
        )
        if audit_failed:
            state["audit_failed"] = audit_failed
        return state

    def disarm_scope(self, scope_type: str, scope_id: str, operator: str) -> dict:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM kill_switch_scopes WHERE scope_type=? AND scope_id=?",
                (scope_type, scope_id),
            )
        audit_failed = self._audit_append(
            "scope_disarmed", {"scope_type": scope_type, "scope_id": scope_id, "operator": operator}
        )
        state = {"scope_type": scope_type, "scope_id": scope_id, "armed": False}
        if audit_failed:
            state["audit_failed"] = audit_failed
        return state

    def scope_state(self, scope_type: str, scope_id: str) -> dict:
        """Read one scope row; fail-closed (armed=True) on unreadable state."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT armed, armed_at, armed_by, reason FROM kill_switch_scopes"
                    " WHERE scope_type=? AND scope_id=?",
                    (scope_type, scope_id),
                ).fetchone()
            if row is None:
                return {"scope_type": scope_type, "scope_id": scope_id, "armed": False}
            return {
                "scope_type": scope_type,
                "scope_id": scope_id,
                "armed": bool(row["armed"]),
                "armed_at": row["armed_at"],
                "armed_by": row["armed_by"],
                "reason": row["reason"],
            }
        except Exception as exc:  # unreadable state => treat as armed
            return {"scope_type": scope_type, "scope_id": scope_id, "armed": True, "fail_closed": True, "reason": str(exc)}

    def list_scopes(self) -> list[dict]:
        """All currently armed scopes (scope rows with armed=1)."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT scope_type, scope_id, armed_at, armed_by, reason"
                    " FROM kill_switch_scopes WHERE armed=1 ORDER BY scope_type, scope_id"
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception as exc:
            return [{"error": str(exc)}]

    def is_blocked(self, scope_type: str | None = None, scope_id: str | None = None) -> bool:
        """Global arm OR a matching scoped arm. Scopes never loosen a global
        lockdown; a scoped arm only blocks its own scope."""
        if self.is_armed():
            return True
        if scope_type is None or scope_id is None:
            return False
        return self.scope_state(scope_type, scope_id)["armed"]

    def require_allowed(self, scope_type: str | None = None, scope_id: str | None = None) -> None:
        """Imperative form: raises GovernanceHalt when blocked for the scope."""
        if self.is_blocked(scope_type, scope_id):
            if scope_type and scope_id:
                raise GovernanceHalt(f"kill switch armed for scope {scope_type}:{scope_id} — work paused")
            raise GovernanceHalt("kill switch armed — loop paused")
