"""SQLite store for the Memory Fabric.

Two tables:

    memory_items          the memories themselves (full provenance columns)
    verification_history  every verification-state transition (who, when,
                          from, to, reason) — the audit trail that makes
                          verification states meaningful

Soft delete: ``forget`` sets ``archived=1`` and appends a DEPRECATED
verification record — the row stays (provenance preserved), recall stops
returning it. Writes are serialized under a per-instance lock (the same
single-process-uvicorn assumption as everywhere else); reads are plain
SQLite queries.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from msb_v3.memory_fabric.models import MemoryItem, MemoryType, VerificationState

_DDL = """
CREATE TABLE IF NOT EXISTS memory_items (
    memory_id       TEXT PRIMARY KEY,
    type            TEXT NOT NULL,
    content         TEXT NOT NULL,
    tags            TEXT NOT NULL DEFAULT '[]',
    importance      REAL NOT NULL DEFAULT 0.5,
    source_agent    TEXT NOT NULL DEFAULT '',
    source          TEXT NOT NULL DEFAULT '',
    task_id         TEXT NOT NULL DEFAULT '',
    tenant          TEXT NOT NULL DEFAULT 'default',
    project         TEXT NOT NULL DEFAULT '',
    tech            TEXT NOT NULL DEFAULT '',
    verification_state TEXT NOT NULL DEFAULT 'UNVERIFIED',
    decay_factor    REAL NOT NULL DEFAULT 0.9,
    relationships   TEXT NOT NULL DEFAULT '[]',
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL,
    last_accessed_at REAL NOT NULL,
    access_count    INTEGER NOT NULL DEFAULT 0,
    archived        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS verification_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id   TEXT NOT NULL,
    from_state  TEXT NOT NULL,
    to_state    TEXT NOT NULL,
    by          TEXT NOT NULL DEFAULT '',
    reason      TEXT NOT NULL DEFAULT '',
    at          REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_mem_tenant_type ON memory_items(tenant, type);
CREATE INDEX IF NOT EXISTS idx_mem_tenant_proj ON memory_items(tenant, project);
CREATE INDEX IF NOT EXISTS idx_mem_archived   ON memory_items(archived);
CREATE INDEX IF NOT EXISTS idx_verif_memory   ON verification_history(memory_id);
"""


def _json_dumps(value: List[str]) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_loads(value: str) -> List[str]:
    try:
        parsed = json.loads(value or "[]")
        return [str(x) for x in parsed] if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


class MemoryFabricStore:
    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock, self._conn() as conn:
            conn.executescript(_DDL)

    # -- writes -----------------------------------------------------------

    def upsert(self, item: MemoryItem) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                """
                INSERT INTO memory_items(
                    memory_id, type, content, tags, importance, source_agent,
                    source, task_id, tenant, project, tech, verification_state,
                    decay_factor, relationships, created_at, updated_at,
                    last_accessed_at, access_count, archived
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(memory_id) DO UPDATE SET
                    content = excluded.content,
                    tags = excluded.tags,
                    importance = excluded.importance,
                    source_agent = excluded.source_agent,
                    source = excluded.source,
                    task_id = excluded.task_id,
                    project = excluded.project,
                    tech = excluded.tech,
                    verification_state = excluded.verification_state,
                    decay_factor = excluded.decay_factor,
                    relationships = excluded.relationships,
                    updated_at = excluded.updated_at,
                    archived = excluded.archived
                """,
                (
                    item.memory_id, item.type.value, item.content,
                    _json_dumps(item.tags), item.importance, item.source_agent,
                    item.source, item.task_id, item.tenant, item.project,
                    item.tech, item.verification_state.value, item.decay_factor,
                    _json_dumps(item.relationships), item.created_at,
                    item.updated_at, item.last_accessed_at, item.access_count,
                    int(item.archived),
                ),
            )

    def record_verification(
        self, memory_id: str, from_state: str, to_state: str, *, by: str = "", reason: str = ""
    ) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO verification_history(memory_id, from_state, to_state, by, reason, at) VALUES (?,?,?,?,?,?)",
                (memory_id, from_state, to_state, by, reason, time.time()),
            )

    def touch(self, memory_id: str) -> None:
        """Record an access (recall): bump last_accessed_at + access_count."""
        with self._lock, self._conn() as conn:
            conn.execute(
                "UPDATE memory_items SET last_accessed_at=?, access_count=access_count+1 WHERE memory_id=?",
                (time.time(), memory_id),
            )

    # -- reads ------------------------------------------------------------

    def get(self, memory_id: str) -> Optional[MemoryItem]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM memory_items WHERE memory_id=?", (memory_id,)
            ).fetchone()
        return self._row_to_item(row) if row else None

    def list_active(
        self,
        tenant: str = "default",
        *,
        type_: Optional[MemoryType] = None,
        project: Optional[str] = None,
        tech: Optional[str] = None,
        limit: int = 100,
    ) -> List[MemoryItem]:
        q = "SELECT * FROM memory_items WHERE tenant=? AND archived=0"
        args: List[Any] = [tenant]
        if type_:
            q += " AND type=?"
            args.append(type_.value)
        if project:
            q += " AND project=?"
            args.append(project)
        if tech:
            q += " AND tech=?"
            args.append(tech)
        q += " ORDER BY importance DESC, updated_at DESC LIMIT ?"
        args.append(limit)
        with self._conn() as conn:
            rows = conn.execute(q, args).fetchall()
        return [self._row_to_item(r) for r in rows]

    def search_keywords(self, tenant: str, query: str, *, limit: int = 50) -> List[MemoryItem]:
        """Literal keyword match over content/tags (the deterministic fallback
        and primary offline path — no embeddings required)."""
        terms = [t for t in query.lower().split() if len(t) >= 2]
        if not terms:
            return []
        clauses = []
        args: List[Any] = [tenant]
        for term in terms:
            clauses.append("(LOWER(content) LIKE ? OR LOWER(tags) LIKE ?)")
            args.extend([f"%{term}%", f"%{term}%"])
        q = (
            "SELECT * FROM memory_items WHERE tenant=? AND archived=0 AND ("
            + " OR ".join(clauses)
            + ") ORDER BY importance DESC, updated_at DESC LIMIT ?"
        )
        args.append(limit)
        with self._conn() as conn:
            rows = conn.execute(q, args).fetchall()
        return [self._row_to_item(r) for r in rows]

    def search_embedding(self, tenant: str, query: str, *, top_k: int = 10) -> List[MemoryItem]:
        """Best-effort semantic search over the tenant's Qdrant collection.

        Lazily imports the RAG seam; any failure (Qdrant down, collection
        missing, embed error) returns [] — the caller falls back to keyword
        scoring. Never raises, never blocks recall. The embed call is
        async; it runs in a worker thread (fresh event loop, the same
        bridge the governed-tool executors use) so this stays callable
        from the sync fabric path.
        """
        try:
            import asyncio
            from concurrent.futures import ThreadPoolExecutor

            from msb_v3.api.rag import _embed, _qdrant_client

            collection = f"tenant_{tenant.replace('/', '_')}"
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix="mfsem") as pool:
                vec = pool.submit(asyncio.run, _embed(query)).result(timeout=30)
            points = _qdrant_client().query_points(
                collection_name=collection, query=vec, limit=top_k, with_payload=True,
            )
        except Exception:
            return []
        items: List[MemoryItem] = []
        for p in getattr(points, "points", []):
            mid = (getattr(p, "payload", {}) or {}).get("memory_id")
            if not mid:
                continue
            item = self.get(str(mid))
            if item is not None and not item.archived:
                items.append(item)
        return items

    def stats(self, tenant: str = "default") -> Dict[str, Any]:
        with self._conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM memory_items WHERE tenant=? AND archived=0", (tenant,)
            ).fetchone()["n"]
            archived = conn.execute(
                "SELECT COUNT(*) AS n FROM memory_items WHERE tenant=? AND archived=1", (tenant,)
            ).fetchone()["n"]
            by_type = {
                r["type"]: r["n"]
                for r in conn.execute(
                    "SELECT type, COUNT(*) AS n FROM memory_items WHERE tenant=? AND archived=0 GROUP BY type",
                    (tenant,),
                ).fetchall()
            }
            by_state = {
                r["verification_state"]: r["n"]
                for r in conn.execute(
                    "SELECT verification_state, COUNT(*) AS n FROM memory_items WHERE tenant=? AND archived=0 GROUP BY verification_state",
                    (tenant,),
                ).fetchall()
            }
            transitions = conn.execute(
                "SELECT COUNT(*) AS n FROM verification_history", ()
            ).fetchone()["n"]
        return {
            "tenant": tenant,
            "active": total,
            "archived": archived,
            "by_type": by_type,
            "by_verification_state": by_state,
            "verification_transitions": transitions,
        }

    def verification_history(self, memory_id: str) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT from_state, to_state, by, reason, at FROM verification_history WHERE memory_id=? ORDER BY at",
                (memory_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def _row_to_item(row: sqlite3.Row) -> MemoryItem:
        return MemoryItem(
            memory_id=row["memory_id"],
            type=MemoryType(row["type"]),
            content=row["content"],
            tags=_json_loads(row["tags"]),
            importance=row["importance"],
            source_agent=row["source_agent"],
            source=row["source"],
            task_id=row["task_id"],
            tenant=row["tenant"],
            project=row["project"],
            tech=row["tech"],
            verification_state=VerificationState(row["verification_state"]),
            decay_factor=row["decay_factor"],
            relationships=_json_loads(row["relationships"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_accessed_at=row["last_accessed_at"],
            access_count=row["access_count"],
            archived=bool(row["archived"]),
        )
