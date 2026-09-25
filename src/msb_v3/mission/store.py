"""Mission store — the versioned spine of the agent control plane.

Blueprint: docs/blueprints/2026-09-25-agent-control-plane.md §4 (Phase 1, §12).

A mission moves through the frozen matrix in ``msb_v3.mission.states``. Every
write — creation, transition, new version — runs inside one SQLite
transaction that commits only after the matching record is on the audit chain
(component ``"missions"``). If the chain refuses, nothing is recorded. This is
deliberately stricter than ``tasks.lifecycle``, which logs a chain failure and
carries on: a mission transition that cannot be evidenced does not happen.
One gap remains: if the chain append succeeds and the SQLite COMMIT then
fails, the chain holds an event the store does not. The chain is the
authoritative record, so that direction errs toward evidence.

What Phase 1 does NOT prove is actor identity. ``actor`` and ``approved_by``
are strings the caller supplies. Operator approval is checked against the
configured operator list (``MSB_MISSION_OPERATORS``) and gate verdicts
against ``GATE_ACTOR``, but nothing here authenticates the caller. That
arrives with the harness registry (Phase 2) and the operator-authenticated
API (Phase 9).
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TypeVar

from msb_ledger.audit_chain import AuditChainLike
from msb_ledger.audit_v2 import CanonicalizationError, canonicalize
from msb_ledger.chain_anchor import anchored_chain_from_env
from msb_v3.core.config import settings
from msb_v3.mission.models import (
    PARENT_KIND,
    Mission,
    MissionVersion,
    StaleVersion,
    VersionKind,
    version_sha256,
)
from msb_v3.mission.states import (
    GATE_VERDICTS,
    TERMINAL,
    MissionState,
    is_human_gated,
    validate_transition,
)

# Same convention as tasks.lifecycle: projections live beside runtime/.
_DB = Path(settings.db_path).parent / "runtime" / "missions.db"

OPERATORS_ENV = "MSB_MISSION_OPERATORS"
GATE_ACTOR = "msb-gate"
CHAIN_COMPONENT = "missions"

# Artifacts a state requires before a mission may enter it.
_REQUIRES_BLUEPRINT = frozenset({MissionState.BLUEPRINT_REVIEW})
_REQUIRES_CURRENT_PLAN = frozenset({MissionState.PLAN_REVIEW, MissionState.BUILDING})

T = TypeVar("T")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS missions (
    mission_id   TEXT PRIMARY KEY,
    objective    TEXT NOT NULL,
    created_by   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    state        TEXT NOT NULL,
    resume_state TEXT,
    constraints  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mission_transitions (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id  TEXT NOT NULL,
    from_state  TEXT NOT NULL,
    to_state    TEXT NOT NULL,
    actor       TEXT NOT NULL,
    approved_by TEXT,
    reason      TEXT NOT NULL,
    at          TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mission_versions (
    mission_id    TEXT NOT NULL,
    kind          TEXT NOT NULL,
    version       INTEGER NOT NULL,
    sha256        TEXT NOT NULL,
    parent_sha256 TEXT,
    created_by    TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    body          TEXT NOT NULL,
    PRIMARY KEY (mission_id, kind, version)
);
CREATE TRIGGER IF NOT EXISTS mission_versions_no_update
    BEFORE UPDATE ON mission_versions
    BEGIN SELECT RAISE(ABORT, 'mission versions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS mission_versions_no_delete
    BEFORE DELETE ON mission_versions
    BEGIN SELECT RAISE(ABORT, 'mission versions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS mission_transitions_no_update
    BEFORE UPDATE ON mission_transitions
    BEGIN SELECT RAISE(ABORT, 'mission transitions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS mission_transitions_no_delete
    BEFORE DELETE ON mission_transitions
    BEGIN SELECT RAISE(ABORT, 'mission transitions are append-only'); END;
"""


class MissionError(RuntimeError):
    """A mission operation was refused; nothing was recorded."""


def operators_from_env() -> frozenset[str]:
    """Operator ids allowed to approve human-gated transitions.

    Comma-separated ``MSB_MISSION_OPERATORS``. Unset or empty means no
    operator is configured, so every human-gated transition is refused
    (fail closed)."""
    raw = os.getenv(OPERATORS_ENV, "")
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical(value: Any, what: str) -> str:
    try:
        return canonicalize(value)
    except CanonicalizationError as exc:
        raise MissionError(f"{what} is not canonical JSON: {exc}") from exc


def _init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_SCHEMA)
    finally:
        conn.close()


class MissionStore:
    def __init__(
        self,
        db_path: str | None = None,
        *,
        chain: AuditChainLike | None = None,
        operators: frozenset[str] | set[str] | None = None,
    ) -> None:
        self.db_path = Path(db_path) if db_path else _DB
        self._chain = chain  # resolved lazily, like tasks.lifecycle
        self._operators = (
            operators_from_env() if operators is None else frozenset(operators)
        )
        _init_db(self.db_path)

    # -- internals ---------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        # isolation_level=None: we issue BEGIN IMMEDIATE / COMMIT ourselves.
        conn = sqlite3.connect(self.db_path, timeout=10.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        return conn

    def _chain_or_default(self) -> AuditChainLike:
        if self._chain is None:
            self._chain = anchored_chain_from_env()
        return self._chain

    def _atomic(
        self,
        step: Callable[[sqlite3.Connection], tuple[str | None, dict[str, Any], T]],
    ) -> T:
        """Run ``step`` in one transaction; commit only once the chain holds
        its event. A step returning ``event_type=None`` wrote nothing and is
        rolled back without touching the chain."""
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            event_type, payload, result = step(conn)
            if event_type is None:
                conn.execute("ROLLBACK")
                return result
            try:
                self._chain_or_default().append(CHAIN_COMPONENT, event_type, payload)
            except Exception as exc:
                raise MissionError(
                    f"audit chain refused {event_type}; nothing was recorded ({exc})"
                ) from exc
            conn.execute("COMMIT")
            return result
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    @staticmethod
    def _row(conn: sqlite3.Connection, mission_id: str) -> sqlite3.Row:
        row = conn.execute(
            "SELECT * FROM missions WHERE mission_id = ?", (mission_id,)
        ).fetchone()
        if row is None:
            raise MissionError(f"unknown mission {mission_id!r}")
        return row

    @staticmethod
    def _mission(row: sqlite3.Row) -> Mission:
        return Mission(
            mission_id=row["mission_id"],
            objective=row["objective"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            state=MissionState(row["state"]),
            resume_state=MissionState(row["resume_state"]) if row["resume_state"] else None,
            constraints=json.loads(row["constraints"]),
        )

    @staticmethod
    def _version(row: sqlite3.Row) -> MissionVersion:
        return MissionVersion(
            mission_id=row["mission_id"],
            kind=VersionKind(row["kind"]),
            version=row["version"],
            sha256=row["sha256"],
            parent_sha256=row["parent_sha256"],
            created_by=row["created_by"],
            created_at=row["created_at"],
            body=json.loads(row["body"]),
        )

    def _latest(
        self, conn: sqlite3.Connection, mission_id: str, kind: VersionKind
    ) -> MissionVersion | None:
        row = conn.execute(
            "SELECT * FROM mission_versions WHERE mission_id = ? AND kind = ? "
            "ORDER BY version DESC LIMIT 1",
            (mission_id, kind.value),
        ).fetchone()
        return self._version(row) if row is not None else None

    def _stale(self, conn: sqlite3.Connection, mission_id: str) -> list[StaleVersion]:
        latest = {kind: self._latest(conn, mission_id, kind) for kind in VersionKind}
        stale: list[StaleVersion] = []
        stale_kinds: set[VersionKind] = set()
        for kind in (VersionKind.PLAN, VersionKind.TASK_GRAPH):
            current = latest[kind]
            parent_kind = PARENT_KIND[kind]
            if current is None or parent_kind is None:
                continue
            parent = latest[parent_kind]
            if parent_kind in stale_kinds:
                reason = f"its {parent_kind.value} is stale"
            elif parent is not None and current.parent_sha256 != parent.sha256:
                reason = (
                    f"built against {parent_kind.value} {str(current.parent_sha256)[:12]}, "
                    f"current {parent_kind.value} is v{parent.version} {parent.sha256[:12]}"
                )
            else:
                continue
            stale.append(StaleVersion(kind=kind, version=current.version, reason=reason))
            stale_kinds.add(kind)
        return stale

    def _check_artifacts(
        self, conn: sqlite3.Connection, mission_id: str, target: MissionState
    ) -> None:
        if target in _REQUIRES_BLUEPRINT and self._latest(
            conn, mission_id, VersionKind.BLUEPRINT
        ) is None:
            raise MissionError(f"{target.value} needs a blueprint version first")
        if target in _REQUIRES_CURRENT_PLAN:
            if self._latest(conn, mission_id, VersionKind.PLAN) is None:
                raise MissionError(f"{target.value} needs a plan version first")
            stale = [s for s in self._stale(conn, mission_id) if s.kind is VersionKind.PLAN]
            if stale:
                raise MissionError(
                    f"{target.value} refused: plan v{stale[0].version} is stale "
                    f"({stale[0].reason})"
                )

    # -- writes ------------------------------------------------------------

    def create(
        self,
        objective: str,
        *,
        created_by: str,
        constraints: dict[str, Any] | None = None,
    ) -> Mission:
        objective = objective.strip()
        if not objective:
            raise MissionError("objective is required")
        if not created_by.strip():
            raise MissionError("created_by is required")
        constraints_json = _canonical(dict(constraints or {}), "constraints")
        now = _now()
        mission = Mission(
            mission_id=f"mission-{uuid.uuid4().hex[:12]}",
            objective=objective,
            created_by=created_by,
            created_at=now,
            updated_at=now,
            state=MissionState.DRAFT,
            resume_state=None,
            constraints=json.loads(constraints_json),
        )

        def step(conn: sqlite3.Connection) -> tuple[str | None, dict[str, Any], Mission]:
            conn.execute(
                "INSERT INTO missions (mission_id, objective, created_by, created_at, "
                "updated_at, state, resume_state, constraints) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, ?)",
                (mission.mission_id, objective, created_by, now, now,
                 mission.state.value, constraints_json),
            )
            payload = {
                "mission_id": mission.mission_id,
                "objective": objective,
                "created_by": created_by,
                "constraints": mission.constraints,
            }
            return "mission.created", payload, mission

        return self._atomic(step)

    def add_version(
        self,
        mission_id: str,
        kind: VersionKind,
        body: dict[str, Any],
        *,
        created_by: str,
    ) -> MissionVersion:
        """Record a new immutable version of ``kind``, built against the
        latest version of its parent kind. Re-adding the latest version's
        exact content against the same parent is a no-op that returns it."""
        if not created_by.strip():
            raise MissionError("created_by is required")
        body_json = _canonical(body, f"{kind.value} body")

        def step(conn: sqlite3.Connection) -> tuple[str | None, dict[str, Any], MissionVersion]:
            state = MissionState(self._row(conn, mission_id)["state"])
            if state in TERMINAL:
                raise MissionError(
                    f"mission {mission_id} is {state.value}; it takes no new versions"
                )
            parent_kind = PARENT_KIND[kind]
            parent_sha: str | None = None
            if parent_kind is not None:
                parent = self._latest(conn, mission_id, parent_kind)
                if parent is None:
                    raise MissionError(
                        f"a {kind.value} is built against a {parent_kind.value}; "
                        "this mission has none yet"
                    )
                parent_sha = parent.sha256
            sha = version_sha256(kind, parent_sha, body)
            latest = self._latest(conn, mission_id, kind)
            if latest is not None and latest.sha256 == sha:
                return None, {}, latest
            version = MissionVersion(
                mission_id=mission_id,
                kind=kind,
                version=1 if latest is None else latest.version + 1,
                sha256=sha,
                parent_sha256=parent_sha,
                created_by=created_by,
                created_at=_now(),
                body=json.loads(body_json),
            )
            conn.execute(
                "INSERT INTO mission_versions (mission_id, kind, version, sha256, "
                "parent_sha256, created_by, created_at, body) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (mission_id, kind.value, version.version, sha, parent_sha,
                 created_by, version.created_at, body_json),
            )
            # The chain carries the hash, not the body: the body lives in the
            # store, and the hash is what proves which body it was.
            payload = {
                "mission_id": mission_id,
                "kind": kind.value,
                "version": version.version,
                "sha256": sha,
                "parent_sha256": parent_sha,
                "created_by": created_by,
            }
            return "mission.version", payload, version

        return self._atomic(step)

    def transition(
        self,
        mission_id: str,
        to_state: MissionState | str,
        *,
        actor: str,
        reason: str,
        approved_by: str | None = None,
    ) -> Mission:
        target = MissionState(to_state)
        if not actor.strip():
            raise MissionError("actor is required")
        if not reason.strip():
            raise MissionError("reason is required")

        def step(conn: sqlite3.Connection) -> tuple[str | None, dict[str, Any], Mission]:
            row = self._row(conn, mission_id)
            current = MissionState(row["state"])
            resume = MissionState(row["resume_state"]) if row["resume_state"] else None
            validate_transition(current, target, resume_state=resume)
            if target in GATE_VERDICTS and actor != GATE_ACTOR:
                raise MissionError(
                    f"only {GATE_ACTOR!r} issues gate verdicts; "
                    f"{actor!r} cannot move a mission to {target.value}"
                )
            if is_human_gated(current, target) and (
                approved_by is None or approved_by not in self._operators
            ):
                raise MissionError(
                    f"{current.value} -> {target.value} needs approval from a "
                    f"configured operator ({OPERATORS_ENV}); got {approved_by!r}"
                )
            self._check_artifacts(conn, mission_id, target)
            new_resume = current if target is MissionState.BLOCKED_ON_HUMAN else None
            now = _now()
            conn.execute(
                "UPDATE missions SET state = ?, resume_state = ?, updated_at = ? "
                "WHERE mission_id = ?",
                (target.value, new_resume.value if new_resume else None, now, mission_id),
            )
            conn.execute(
                "INSERT INTO mission_transitions (mission_id, from_state, to_state, "
                "actor, approved_by, reason, at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (mission_id, current.value, target.value, actor, approved_by, reason, now),
            )
            payload = {
                "mission_id": mission_id,
                "from_state": current.value,
                "to_state": target.value,
                "actor": actor,
                "approved_by": approved_by,
                "reason": reason,
            }
            return "mission.transition", payload, self._mission(self._row(conn, mission_id))

        return self._atomic(step)

    # -- reads -------------------------------------------------------------

    def get(self, mission_id: str) -> Mission:
        conn = self._connect()
        try:
            return self._mission(self._row(conn, mission_id))
        finally:
            conn.close()

    def latest_version(self, mission_id: str, kind: VersionKind) -> MissionVersion | None:
        conn = self._connect()
        try:
            self._row(conn, mission_id)
            return self._latest(conn, mission_id, kind)
        finally:
            conn.close()

    def versions(
        self, mission_id: str, kind: VersionKind | None = None
    ) -> list[MissionVersion]:
        conn = self._connect()
        try:
            self._row(conn, mission_id)
            if kind is None:
                rows = conn.execute(
                    "SELECT * FROM mission_versions WHERE mission_id = ? "
                    "ORDER BY kind, version",
                    (mission_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM mission_versions WHERE mission_id = ? AND kind = ? "
                    "ORDER BY version",
                    (mission_id, kind.value),
                ).fetchall()
            return [self._version(r) for r in rows]
        finally:
            conn.close()

    def stale_versions(self, mission_id: str) -> list[StaleVersion]:
        """Latest plan / task graph built against something that is no
        longer current. Staleness propagates: a task graph over a stale plan
        is stale."""
        conn = self._connect()
        try:
            self._row(conn, mission_id)
            return self._stale(conn, mission_id)
        finally:
            conn.close()

    def history(self, mission_id: str) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            self._row(conn, mission_id)
            rows = conn.execute(
                "SELECT seq, from_state, to_state, actor, approved_by, reason, at "
                "FROM mission_transitions WHERE mission_id = ? ORDER BY seq",
                (mission_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
