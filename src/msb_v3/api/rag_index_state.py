"""SQLite control-plane state for staged, manifest-verified RAG snapshots."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from msb_v3.core.config import settings

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect() -> sqlite3.Connection:
    path = Path(settings.db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS rag_managed_tenants (
            tenant_id TEXT PRIMARY KEY,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS rag_index_snapshots (
            snapshot_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            collection_name TEXT NOT NULL UNIQUE,
            manifest_sha256 TEXT NOT NULL,
            expected_points INTEGER NOT NULL,
            source_count INTEGER NOT NULL,
            base_snapshot_id TEXT,
            verify_sources INTEGER NOT NULL DEFAULT 0,
            source_root TEXT,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL,
            activated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS rag_snapshot_manifest (
            snapshot_id TEXT NOT NULL REFERENCES rag_index_snapshots(snapshot_id) ON DELETE CASCADE,
            source TEXT NOT NULL,
            chunk INTEGER NOT NULL,
            content_sha256 TEXT NOT NULL,
            source_sha256 TEXT NOT NULL,
            uploaded INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (snapshot_id, source, chunk)
        );
        CREATE TABLE IF NOT EXISTS rag_active_indexes (
            tenant_id TEXT PRIMARY KEY,
            snapshot_id TEXT NOT NULL REFERENCES rag_index_snapshots(snapshot_id),
            collection_name TEXT NOT NULL,
            manifest_sha256 TEXT NOT NULL,
            expected_points INTEGER NOT NULL,
            source_count INTEGER NOT NULL,
            activated_at TEXT NOT NULL
        );
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(rag_index_snapshots)")}
    if "verify_sources" not in columns:
        conn.execute("ALTER TABLE rag_index_snapshots ADD COLUMN verify_sources INTEGER NOT NULL DEFAULT 0")
    if "source_root" not in columns:
        conn.execute("ALTER TABLE rag_index_snapshots ADD COLUMN source_root TEXT")
    return conn


def _safe_source(source: Any) -> bool:
    if not isinstance(source, str) or not source or "\\" in source or "\x00" in source:
        return False
    raw_parts = source.split("/")
    if any(part in ("", ".", "..") for part in raw_parts):
        return False
    path = PurePosixPath(source)
    return not path.is_absolute() and path.as_posix() == source


def _normalize_manifest(manifest: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for entry in manifest:
        source = entry.get("source")
        chunk = entry.get("chunk")
        content_hash = entry.get("content_sha256")
        source_hash = entry.get("source_sha256")
        if not _safe_source(source):
            raise ValueError(f"unsafe relative POSIX source: {source!r}")
        if not isinstance(chunk, int) or isinstance(chunk, bool) or chunk < 0:
            raise ValueError(f"invalid chunk number for {source!r}")
        assert isinstance(source, str)
        if not isinstance(content_hash, str) or not _HASH_RE.fullmatch(content_hash):
            raise ValueError(f"invalid content SHA-256 for {source}#{chunk}")
        if not isinstance(source_hash, str) or not _HASH_RE.fullmatch(source_hash):
            raise ValueError(f"invalid source SHA-256 for {source}")
        key = (source, chunk)
        if key in seen:
            raise ValueError(f"duplicate manifest entry: {source}#{chunk}")
        seen.add(key)
        normalized.append({"source": source, "chunk": chunk,
                           "content_sha256": content_hash, "source_sha256": source_hash})
    per_source: dict[str, set[str]] = {}
    for entry in normalized:
        per_source.setdefault(entry["source"], set()).add(entry["source_sha256"])
    if any(len(hashes) != 1 for hashes in per_source.values()):
        raise ValueError("all chunks from one source must carry the same source_sha256")
    chunks_by_source: dict[str, list[int]] = {}
    for source, chunk in seen:
        chunks_by_source.setdefault(source, []).append(chunk)
    if any(sorted(chunks) != list(range(len(chunks))) for chunks in chunks_by_source.values()):
        raise ValueError("chunks for each source must be contiguous from zero")
    return sorted(normalized, key=lambda item: (item["source"], item["chunk"]))


def manifest_sha256(manifest: list[dict[str, Any]]) -> str:
    payload = json.dumps(_normalize_manifest(manifest), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def begin_snapshot(
    tenant_id: str,
    manifest: list[dict[str, Any]],
    *,
    verify_sources: bool = False,
    source_root: str | None = None,
) -> dict[str, Any]:
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise ValueError("tenant_id must not be empty")
    entries = _normalize_manifest(manifest)
    if not entries:
        raise ValueError("refusing to publish an empty source manifest")
    if verify_sources and (not isinstance(source_root, str) or not source_root.strip()):
        raise ValueError("source_root is required when verify_sources is enabled")
    snapshot_id = uuid.uuid4().hex
    collection_name = f"rag_snapshot_{snapshot_id}"
    digest = manifest_sha256(entries)
    source_count = len({entry["source"] for entry in entries})
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        active = conn.execute("SELECT snapshot_id FROM rag_active_indexes WHERE tenant_id = ?", (tenant_id,)).fetchone()
        base_id = active["snapshot_id"] if active else None
        conn.execute(
            """INSERT INTO rag_index_snapshots
               (snapshot_id, tenant_id, collection_name, manifest_sha256, expected_points,
                source_count, base_snapshot_id, verify_sources, source_root, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'building', ?)""",
            (snapshot_id, tenant_id, collection_name, digest, len(entries), source_count,
             base_id, int(verify_sources), source_root, _now()),
        )
        conn.executemany(
            """INSERT INTO rag_snapshot_manifest
               (snapshot_id, source, chunk, content_sha256, source_sha256, uploaded)
               VALUES (?, ?, ?, ?, ?, 0)""",
            [(snapshot_id, e["source"], e["chunk"], e["content_sha256"], e["source_sha256"]) for e in entries],
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"snapshot_id": snapshot_id, "collection_name": collection_name,
            "manifest_sha256": digest, "expected_points": len(entries),
            "source_count": source_count, "base_snapshot_id": base_id,
            "verify_sources": verify_sources, "source_root": source_root}


def get_snapshot(snapshot_id: str, tenant_id: str) -> dict[str, Any] | None:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM rag_index_snapshots WHERE snapshot_id = ? AND tenant_id = ?",
                           (snapshot_id, tenant_id)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def active_snapshot(tenant_id: str) -> dict[str, Any] | None:
    conn = _connect()
    try:
        row = conn.execute(
            """SELECT a.*, s.verify_sources, s.source_root, s.status AS snapshot_status
               FROM rag_active_indexes AS a
               JOIN rag_index_snapshots AS s ON s.snapshot_id = a.snapshot_id
               WHERE a.tenant_id = ?""",
            (tenant_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def tenant_is_managed(tenant_id: str) -> bool:
    conn = _connect()
    try:
        return conn.execute(
            """SELECT 1 FROM rag_managed_tenants WHERE tenant_id = ?
               UNION ALL SELECT 1 FROM rag_active_indexes WHERE tenant_id = ? LIMIT 1""",
            (tenant_id, tenant_id),
        ).fetchone() is not None
    finally:
        conn.close()


def validate_snapshot_batch(snapshot_id: str, tenant_id: str, documents: list[dict[str, Any]]) -> None:
    seen: set[tuple[str, int]] = set()
    for doc in documents:
        source, chunk, text = doc.get("source"), doc.get("chunk", 0), doc.get("text", "")
        if not _safe_source(source) or not isinstance(chunk, int) or isinstance(chunk, bool) or chunk < 0:
            raise ValueError("document requires safe relative source and non-negative chunk")
        assert isinstance(source, str)
        if not isinstance(text, str):
            raise ValueError(f"document text must be a string: {source}#{chunk}")
        key = (source, chunk)
        if key in seen:
            raise ValueError(f"duplicate document in batch: {source}#{chunk}")
        seen.add(key)
    conn = _connect()
    try:
        snap = conn.execute("SELECT tenant_id, status FROM rag_index_snapshots WHERE snapshot_id = ?",
                            (snapshot_id,)).fetchone()
        if not snap or snap["tenant_id"] != tenant_id:
            raise ValueError("snapshot not found for tenant")
        if snap["status"] != "building":
            raise ValueError(f"snapshot is not writable ({snap['status']})")
        for doc in documents:
            content_hash = hashlib.sha256(doc["text"].encode("utf-8")).hexdigest()
            row = conn.execute(
                """SELECT content_sha256, source_sha256 FROM rag_snapshot_manifest
                   WHERE snapshot_id = ? AND source = ? AND chunk = ?""",
                (snapshot_id, doc["source"], doc.get("chunk", 0)),
            ).fetchone()
            if not row or content_hash != row["content_sha256"] or doc.get("source_sha256") != row["source_sha256"]:
                raise ValueError(f"document does not match manifest: {doc['source']}#{doc.get('chunk', 0)}")
    finally:
        conn.close()


def claim_snapshot_batch(snapshot_id: str, tenant_id: str, documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reserve manifest entries and return only entries needing an upsert."""
    validate_snapshot_batch(snapshot_id, tenant_id, documents)
    conn = _connect()
    claimed: list[dict[str, Any]] = []
    try:
        conn.execute("BEGIN IMMEDIATE")
        for doc in documents:
            source, chunk = doc["source"], doc.get("chunk", 0)
            row = conn.execute("SELECT uploaded FROM rag_snapshot_manifest WHERE snapshot_id=? AND source=? AND chunk=?",
                               (snapshot_id, source, chunk)).fetchone()
            if row["uploaded"] == 1:
                continue
            if row["uploaded"] == 2:
                raise ValueError(f"upload already in flight: {source}#{chunk}")
            cur = conn.execute("UPDATE rag_snapshot_manifest SET uploaded=2 WHERE snapshot_id=? AND source=? AND chunk=? AND uploaded=0",
                               (snapshot_id, source, chunk))
            if cur.rowcount != 1:
                raise ValueError(f"could not reserve manifest entry: {source}#{chunk}")
            claimed.append(doc)
        conn.commit()
        return claimed
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def record_uploaded(snapshot_id: str, tenant_id: str, documents: list[dict[str, Any]]) -> None:
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        snap = conn.execute("SELECT status FROM rag_index_snapshots WHERE snapshot_id=? AND tenant_id=?",
                            (snapshot_id, tenant_id)).fetchone()
        if not snap or snap["status"] != "building":
            raise ValueError("snapshot ceased to be writable")
        for doc in documents:
            cur = conn.execute("UPDATE rag_snapshot_manifest SET uploaded=1 WHERE snapshot_id=? AND source=? AND chunk=? AND uploaded=2",
                               (snapshot_id, doc["source"], doc.get("chunk", 0)))
            if cur.rowcount != 1:
                raise ValueError(f"upload claim missing: {doc['source']}#{doc.get('chunk', 0)}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def release_snapshot_batch(snapshot_id: str, documents: list[dict[str, Any]]) -> None:
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.executemany("UPDATE rag_snapshot_manifest SET uploaded=0 WHERE snapshot_id=? AND source=? AND chunk=? AND uploaded=2",
                         [(snapshot_id, d["source"], d.get("chunk", 0)) for d in documents])
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def validate_snapshot_hits(snapshot_id: str, tenant_id: str, hits: list[dict[str, Any]]) -> None:
    conn = _connect()
    try:
        snap = conn.execute("SELECT tenant_id, status FROM rag_index_snapshots WHERE snapshot_id=?",
                            (snapshot_id,)).fetchone()
        if not snap or snap["tenant_id"] != tenant_id or snap["status"] != "active":
            raise ValueError("retrieved snapshot is not active")
        for hit in hits:
            source, chunk = hit.get("source"), hit.get("chunk")
            text = str(hit.get("text", ""))
            expected = conn.execute(
                """SELECT content_sha256, source_sha256 FROM rag_snapshot_manifest
                   WHERE snapshot_id=? AND source=? AND chunk=? AND uploaded=1""",
                (snapshot_id, source, chunk),
            ).fetchone()
            actual = hashlib.sha256(text.encode("utf-8")).hexdigest()
            meta = hit.get("metadata", {})
            if not expected or expected["content_sha256"] != actual:
                raise ValueError(f"retrieved point does not match active manifest: {source}#{chunk}")
            if (meta.get("_rag_content_sha256") != actual
                    or meta.get("_rag_source_sha256") != expected["source_sha256"]
                    or meta.get("_rag_snapshot_id") != snapshot_id):
                raise ValueError(f"retrieved point hash/provenance metadata mismatch: {source}#{chunk}")
    finally:
        conn.close()


def snapshot_collections(tenant_id: str) -> list[str]:
    conn = _connect()
    try:
        rows = conn.execute("SELECT collection_name FROM rag_index_snapshots WHERE tenant_id = ?",
                            (tenant_id,)).fetchall()
        return [row["collection_name"] for row in rows]
    finally:
        conn.close()


def snapshot_manifest(snapshot_id: str) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        rows = conn.execute("SELECT source, chunk, content_sha256, source_sha256, uploaded FROM rag_snapshot_manifest WHERE snapshot_id=? ORDER BY source,chunk",
                            (snapshot_id,)).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def commit_snapshot(snapshot_id: str, tenant_id: str, qdrant_points: int) -> tuple[dict[str, Any], str | None]:
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM rag_index_snapshots WHERE snapshot_id=? AND tenant_id=?",
                           (snapshot_id, tenant_id)).fetchone()
        if not row or row["status"] != "building":
            raise ValueError("snapshot not found or not building")
        uploaded = conn.execute("SELECT COUNT(*) AS n FROM rag_snapshot_manifest WHERE snapshot_id=? AND uploaded=1",
                                (snapshot_id,)).fetchone()["n"]
        if uploaded != row["expected_points"] or qdrant_points != row["expected_points"]:
            raise ValueError(f"incomplete snapshot: expected={row['expected_points']} uploaded={uploaded} points={qdrant_points}")
        active = conn.execute("SELECT * FROM rag_active_indexes WHERE tenant_id=?", (tenant_id,)).fetchone()
        active_id = active["snapshot_id"] if active else None
        if active_id != row["base_snapshot_id"]:
            raise ValueError("snapshot base is stale; another snapshot activated during this build")
        now = _now()
        previous_collection = active["collection_name"] if active else None
        if active:
            conn.execute("UPDATE rag_index_snapshots SET status='retired' WHERE snapshot_id=?", (active_id,))
        conn.execute(
            """INSERT INTO rag_active_indexes VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(tenant_id) DO UPDATE SET snapshot_id=excluded.snapshot_id,
               collection_name=excluded.collection_name, manifest_sha256=excluded.manifest_sha256,
               expected_points=excluded.expected_points, source_count=excluded.source_count,
               activated_at=excluded.activated_at""",
            (tenant_id, snapshot_id, row["collection_name"], row["manifest_sha256"],
             row["expected_points"], row["source_count"], now),
        )
        conn.execute("UPDATE rag_index_snapshots SET status='active', activated_at=? WHERE snapshot_id=?", (now, snapshot_id))
        conn.execute("INSERT OR IGNORE INTO rag_managed_tenants (tenant_id, created_at) VALUES (?, ?)",
                     (tenant_id, now))
        conn.commit()
        active_info = {"snapshot_id": snapshot_id, "collection_name": row["collection_name"],
                       "manifest_sha256": row["manifest_sha256"], "expected_points": row["expected_points"],
                       "source_count": row["source_count"], "activated_at": now}
        return active_info, previous_collection
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def abort_snapshot(snapshot_id: str, tenant_id: str) -> dict[str, Any] | None:
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM rag_index_snapshots WHERE snapshot_id=? AND tenant_id=?",
                           (snapshot_id, tenant_id)).fetchone()
        if not row:
            conn.commit()
            return None
        if row["status"] == "aborted":
            conn.commit()
            return dict(row)
        if row["status"] != "building":
            raise ValueError(f"cannot abort snapshot in state {row['status']}")
        conn.execute("UPDATE rag_index_snapshots SET status='aborted' WHERE snapshot_id=?", (snapshot_id,))
        conn.commit()
        return dict(row)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def clear_tenant_state(tenant_id: str) -> None:
    """Test helper. Qdrant collections must be removed separately by the caller."""
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM rag_active_indexes WHERE tenant_id=?", (tenant_id,))
        conn.execute("DELETE FROM rag_index_snapshots WHERE tenant_id=?", (tenant_id,))
        conn.execute("DELETE FROM rag_managed_tenants WHERE tenant_id=?", (tenant_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
