"""Agent identity (unified-architecture §31 items 16-17).

Every worker — local slice, CLI agent (Claude Code / Codex / OpenCode), or a
future Paseo adapter — is registered as an ``AgentIdentity`` before it may
run. Identity binds the standing permission grant:

    granted_capabilities  — the capability-scoped permissions (§17); the
                            action gate BLOCKS anything outside this set
    tenant_scope          — which tenant the agent may touch ("*" = all)
    autonomy_level        — L0..L5 autonomy ladder (§21)
    max_risk_tier         — the highest action tier this agent may take

``fingerprint`` is a content hash over the identity's authorization-relevant
fields (provider, model, capabilities, autonomy, risk tier, tenant scope), so
a change to any of them is detectable — an agent that drifted from its
registered grant is distinguishable from the one that was approved.

The registry is a durable sqlite projection (runtime-store convention); the
audit chain records registration/revocation events (authoritative).
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from msb_v3.core.config import settings

logger = logging.getLogger(__name__)

_DB = Path(settings.db_path).parent / "runtime" / "agents.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _j(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _un(value: str, default: Any = None) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def compute_fingerprint(
    *,
    provider_id: str,
    model: str,
    granted_capabilities: tuple[str, ...],
    tenant_scope: str,
    autonomy_level: int,
    max_risk_tier: int,
) -> str:
    """Content hash over the authorization-relevant identity fields."""
    payload = _j(
        {
            "provider_id": provider_id,
            "model": model,
            "granted_capabilities": sorted(granted_capabilities),
            "tenant_scope": tenant_scope,
            "autonomy_level": int(autonomy_level),
            "max_risk_tier": int(max_risk_tier),
        }
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class AgentIdentity:
    agent_id: str
    name: str
    kind: str  # local | cli | paseo
    provider_id: str
    model: str = "local"
    granted_capabilities: tuple[str, ...] = ()
    tenant_scope: str = "*"
    autonomy_level: int = 0  # L0..L5 (§21)
    max_risk_tier: int = 2
    created_at: str = field(default_factory=_now)
    revoked: bool = False
    fingerprint: str = ""

    def __post_init__(self) -> None:
        if not self.fingerprint:
            object.__setattr__(
                self,
                "fingerprint",
                compute_fingerprint(
                    provider_id=self.provider_id,
                    model=self.model,
                    granted_capabilities=self.granted_capabilities,
                    tenant_scope=self.tenant_scope,
                    autonomy_level=self.autonomy_level,
                    max_risk_tier=self.max_risk_tier,
                ),
            )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "name": self.name,
            "kind": self.kind,
            "provider_id": self.provider_id,
            "model": self.model,
            "granted_capabilities": list(self.granted_capabilities),
            "tenant_scope": self.tenant_scope,
            "autonomy_level": self.autonomy_level,
            "max_risk_tier": self.max_risk_tier,
            "created_at": self.created_at,
            "revoked": self.revoked,
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AgentIdentity":
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        known["granted_capabilities"] = tuple(data.get("granted_capabilities", []))
        return cls(**known)

    def has_capability(self, capability: str) -> bool:
        return not self.revoked and capability in self.granted_capabilities


def _init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_identity (
                agent_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                provider_id TEXT NOT NULL,
                model TEXT NOT NULL,
                granted_capabilities TEXT NOT NULL,
                tenant_scope TEXT NOT NULL,
                autonomy_level INTEGER NOT NULL,
                max_risk_tier INTEGER NOT NULL,
                fingerprint TEXT NOT NULL,
                created_at TEXT NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0
            )
            """
        )


class AgentRegistry:
    """Durable agent identity registry with capability-scoped permissions."""

    def __init__(self, db_path: Optional[str] = None, *, audit_chain: Any = None) -> None:
        self.db_path = Path(db_path) if db_path else _DB
        self._chain = audit_chain
        _init_db(self.db_path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _audit(self, event_type: str, payload: Dict[str, Any]) -> None:
        try:
            chain = self._chain
            if chain is None:
                from msb_v3.uac.chain_anchor import anchored_chain_from_env

                chain = anchored_chain_from_env()
            chain.append("agents", event_type, payload)
        except Exception as exc:  # noqa: BLE001 — registration must not fail on an audit outage
            logger.warning("agent audit append failed (%s): %s", event_type, exc)

    def register(self, identity: AgentIdentity) -> Dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO agent_identity(
                    agent_id, name, kind, provider_id, model, granted_capabilities,
                    tenant_scope, autonomy_level, max_risk_tier, fingerprint,
                    created_at, revoked
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    identity.agent_id,
                    identity.name,
                    identity.kind,
                    identity.provider_id,
                    identity.model,
                    _j(list(identity.granted_capabilities)),
                    identity.tenant_scope,
                    int(identity.autonomy_level),
                    int(identity.max_risk_tier),
                    identity.fingerprint,
                    identity.created_at,
                    1 if identity.revoked else 0,
                ),
            )
        self._audit("agent.registered", {"agent_id": identity.agent_id, "fingerprint": identity.fingerprint})
        return identity.as_dict()

    def get(self, agent_id: str) -> AgentIdentity:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM agent_identity WHERE agent_id=?", (agent_id,)
            ).fetchone()
        if row is None:
            raise KeyError(f"unknown agent: {agent_id}")
        return AgentIdentity.from_dict(
            {
                "agent_id": row["agent_id"],
                "name": row["name"],
                "kind": row["kind"],
                "provider_id": row["provider_id"],
                "model": row["model"],
                "granted_capabilities": tuple(_un(row["granted_capabilities"], [])),
                "tenant_scope": row["tenant_scope"],
                "autonomy_level": row["autonomy_level"],
                "max_risk_tier": row["max_risk_tier"],
                "fingerprint": row["fingerprint"],
                "created_at": row["created_at"],
                "revoked": bool(row["revoked"]),
            }
        )

    def revoke(self, agent_id: str, operator: str = "operator") -> Dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT 1 FROM agent_identity WHERE agent_id=?", (agent_id,)).fetchone()
            if row is None:
                raise KeyError(f"unknown agent: {agent_id}")
            conn.execute("UPDATE agent_identity SET revoked=1 WHERE agent_id=?", (agent_id,))
        self._audit("agent.revoked", {"agent_id": agent_id, "operator": operator})
        return self.get(agent_id).as_dict()

    def list(self, include_revoked: bool = False) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if include_revoked:
                rows = conn.execute("SELECT agent_id FROM agent_identity ORDER BY created_at").fetchall()
            else:
                rows = conn.execute(
                    "SELECT agent_id FROM agent_identity WHERE revoked=0 ORDER BY created_at"
                ).fetchall()
        return [self.get(r["agent_id"]).as_dict() for r in rows]

    def has_capability(self, agent_id: str, capability: str) -> bool:
        try:
            identity = self.get(agent_id)
        except KeyError:
            return False
        return identity.has_capability(capability)
