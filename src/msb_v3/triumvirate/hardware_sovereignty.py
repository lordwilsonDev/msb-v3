"""Hardware Sovereignty — cluster-aware mesh + local vector hippocampus."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from msb_v3.core.config import settings

_RUNTIME_ROOT = Path(settings.db_path).parent / "triumvirate"
_MESH_STATE_FILE = _RUNTIME_ROOT / "mesh_state.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_mesh_state() -> Dict[str, Any]:
    if not _MESH_STATE_FILE.exists():
        return {"peers": {}, "cluster": {}}
    try:
        return json.loads(_MESH_STATE_FILE.read_text())
    except Exception:
        return {"peers": {}, "cluster": {}}


def _save_mesh_state(state: Dict[str, Any]) -> None:
    _RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    _MESH_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2))


@dataclass
class PeerNode:
    node_id: str
    host: str
    port: int
    capacity: int = 1
    cluster_role: str = "worker"
    last_seen: str = field(default_factory=_now_iso)


class ClusterAwareDiscovery:
    def register_peer(self, node: PeerNode) -> Dict[str, Any]:
        state = _load_mesh_state()
        state["peers"][node.node_id] = {
            "host": node.host,
            "port": node.port,
            "capacity": node.capacity,
            "cluster_role": node.cluster_role,
            "last_seen": node.last_seen,
        }
        _save_mesh_state(state)
        return state["peers"][node.node_id]

    def peers(self) -> List[Dict[str, Any]]:
        state = _load_mesh_state()
        return [{"node_id": k, **v} for k, v in state["peers"].items()]


@dataclass
class DocumentChunk:
    doc_id: str
    chunk_id: str
    text: str
    embedding: List[float]
    metadata: Dict[str, Any] = field(default_factory=dict)


class VectorHippocampus:
    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = Path(db_path) if db_path else _RUNTIME_ROOT / "hippocampus.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS chunks (
                    doc_id TEXT NOT NULL,
                    chunk_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    embedding BLOB NOT NULL,
                    metadata TEXT NOT NULL DEFAULT '{}',
                    ts REAL NOT NULL,
                    PRIMARY KEY (doc_id, chunk_id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id)"
            )

    def _serialize_embedding(self, embedding: List[float]) -> bytes:
        return json.dumps(embedding).encode()

    def _deserialize_embedding(self, data: bytes) -> List[float]:
        try:
            return json.loads(data)
        except Exception:
            return []

    def upsert(self, chunk: DocumentChunk) -> None:
        with self._conn() as conn:
            conn.execute(
                "REPLACE INTO chunks(doc_id, chunk_id, text, embedding, metadata, ts) VALUES (?,?,?,?,?,?)",
                (
                    chunk.doc_id,
                    chunk.chunk_id,
                    chunk.text,
                    self._serialize_embedding(chunk.embedding),
                    json.dumps(chunk.metadata or {}, ensure_ascii=False),
                    datetime.now(timezone.utc).timestamp(),
                ),
            )

    def search(self, query_embedding: List[float], limit: int = 5) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT doc_id, chunk_id, text, embedding, metadata FROM chunks ORDER BY ts DESC LIMIT ?",
                (limit * 10,),
            ).fetchall()
        scored = []
        for row in rows:
            embedding = self._deserialize_embedding(row["embedding"])
            score = self._cosine(query_embedding, embedding)
            scored.append({
                "doc_id": row["doc_id"],
                "chunk_id": row["chunk_id"],
                "text": row["text"],
                "score": score,
                "metadata": json.loads(row["metadata"] or "{}"),
            })
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:limit]

    @staticmethod
    def _cosine(a: List[float], b: List[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = sum(x * x for x in a) ** 0.5
        nb = sum(y * y for y in b) ** 0.5
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)
