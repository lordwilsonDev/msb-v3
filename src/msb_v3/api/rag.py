"""Tenant-scoped RAG API backed by Qdrant with Ollama embeddings."""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from msb_v3.api import rag_index_state
from msb_v3.observability.metrics import TRIUMVIRATE_HIPPOCAMPUS

logger = logging.getLogger(__name__)
router = APIRouter()

try:
    from qdrant_client import QdrantClient
    _HAS_QDRANT = True
except Exception:  # pragma: no cover
    _HAS_QDRANT = False

def _default_ollama_url() -> str:
    """App-wide ollama URL without importing settings at module import time
    (rag.py is imported before core.config in some test paths)."""
    from msb_v3.core.config import settings

    return settings.ollama_url


def _ollama_base() -> str:
    """Ollama base URL for embeddings: explicit OLLAMA_HOST override wins,
    else the app-wide OLLAMA_URL (settings). Never a hardcoded localhost —
    found live in the close-out Phase 1 container test: a container pointed at
    the host ollama via OLLAMA_URL still sent embedding calls to itself and
    500'd. Resolved per call so env changes are honored without a reload.
    """
    return os.getenv("OLLAMA_HOST") or _default_ollama_url()


_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")
_EMBED_DIM = 768


def _qdrant_client() -> Any:
    if not _HAS_QDRANT:
        raise RuntimeError("qdrant client not available")
    host = os.getenv("QDRANT_HOST", "localhost")
    port = int(os.getenv("QDRANT_PORT", "6333"))
    return QdrantClient(host=host, port=port, prefer_grpc=False)


def _collection(tenant_id: str) -> str:
    safe = tenant_id.replace("/", "_").replace(":", "_").replace(" ", "_")
    return f"tenant_{safe}"


def _ensure_collection(client: Any, collection: str) -> None:
    """Create the collection only if it is missing.

    The old code called ``create_collection`` unconditionally on every
    ``/index`` request and logged the resulting 409 ("already exists") at
    warning level — 318 error-level lines per JOB-004. Check first; only warn
    on a genuine create failure (an unreachable server still fails loudly on
    the subsequent upsert).
    """
    try:
        exists = client.collection_exists(collection)
    except Exception:  # noqa: BLE001 — older client / transport hiccup: fall through to create
        exists = False
    if exists:
        return
    try:
        client.create_collection(
            collection_name=collection,
            vectors_config={"size": _EMBED_DIM, "distance": "Cosine"},
        )
    except Exception as exc:  # noqa: BLE001
        msg = str(exc).lower()
        if "already exists" in msg or "409" in msg or "conflict" in msg:
            return
        logger.warning("failed to create collection %s: %s", collection, exc)


_TEST_TENANT_PREFIXES = ("live_test_", "r02_eval_")
_CHUNK_SIZE = 3000
_CHUNK_OVERLAP = 200


def _split_vault_text(text: str) -> list[str]:
    if not text.strip():
        return []
    if len(text) <= _CHUNK_SIZE:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        chunks.append(text[start:start + _CHUNK_SIZE])
        if start + _CHUNK_SIZE >= len(text):
            break
        start += _CHUNK_SIZE - _CHUNK_OVERLAP
    return chunks


def _snapshot_source_root(state: dict[str, Any]) -> Path:
    from msb_v3.core.config import settings

    if state["tenant_id"] == "wilson-vault":
        return Path(settings.vault_path).expanduser().resolve()
    if (state["tenant_id"].startswith(_TEST_TENANT_PREFIXES)
            and state.get("source_root")
            and Path(state["source_root"]).expanduser().resolve()
            == Path(settings.vault_path).expanduser().resolve()):
        return Path(state["source_root"]).expanduser().resolve()
    raise RuntimeError("source root is not authorized for currentness verification")


def _delete_qdrant_collection(client: Any, collection: str) -> bool:
    try:
        try:
            if not client.collection_exists(collection):
                return True
        except AttributeError:  # compatibility with older clients and test doubles
            pass
        client.delete_collection(collection_name=collection)
        return True
    except Exception as exc:  # noqa: BLE001 — cleanup must not undo an activation
        logger.warning("failed to remove retired RAG collection %s: %s", collection, exc)
        return False


def _tree_manifest(root_path: Path) -> list[dict[str, Any]]:
    """Rebuild the same source/chunk hashes as the vault reindexers."""
    root = root_path.resolve()
    if not root.is_dir():
        raise RuntimeError(f"source root is unavailable: {root}")
    excluded = {"Claude-Conversations", "Claude-Projects", ".obsidian", ".git", "Backups"}
    manifest: list[dict[str, Any]] = []
    paths: list[Path] = []

    def _walk_error(exc: OSError) -> None:
        raise RuntimeError("could not enumerate configured source tree") from exc

    for directory, dirnames, filenames in os.walk(root, topdown=True, onerror=_walk_error, followlinks=False):
        dirnames[:] = sorted(name for name in dirnames if name not in excluded)
        for filename in filenames:
            if filename.endswith(".md"):
                paths.append(Path(directory) / filename)
    for path in sorted(paths):
        try:
            relative = path.relative_to(root)
            if path.is_symlink():
                resolved = path.resolve(strict=True)
                if not resolved.is_relative_to(root):
                    raise RuntimeError(f"source symlink escapes configured root: {relative.as_posix()}")
            else:
                resolved = path
            raw = resolved.read_bytes()
        except OSError as exc:
            raise RuntimeError(f"could not verify source {path}") from exc
        text = raw.decode("utf-8", errors="ignore")
        chunks = _split_vault_text(text)
        if not chunks:
            continue
        source = relative.as_posix()
        source_hash = hashlib.sha256(raw).hexdigest()
        for index, chunk_text in enumerate(chunks):
            manifest.append({
                "source": source,
                "chunk": index,
                "content_sha256": hashlib.sha256(chunk_text.encode("utf-8")).hexdigest(),
                "source_sha256": source_hash,
            })
    return manifest


def _snapshot_matches_root(snapshot_id: str, source_root: Path) -> tuple[bool, list[str]]:
    expected = rag_index_state.snapshot_manifest(snapshot_id)
    actual = _tree_manifest(source_root)
    expected_map = {(x["source"], x["chunk"]): (x["content_sha256"], x["source_sha256"]) for x in expected}
    actual_map = {(x["source"], x["chunk"]): (x["content_sha256"], x["source_sha256"]) for x in actual}
    changed = {key[0] for key in expected_map.keys() | actual_map.keys()
               if expected_map.get(key) != actual_map.get(key)}
    return not changed, sorted(changed)


def _verify_snapshot_collection(
    client: Any, state: dict[str, Any], *, verify_sources: bool = True
) -> int:
    """Independently verify every stored point against its frozen manifest."""
    manifest = rag_index_state.snapshot_manifest(state["snapshot_id"])
    expected = {(m["source"], m["chunk"]): m for m in manifest}
    seen: set[tuple[str, int]] = set()
    offset = None
    while True:
        page, offset = client.scroll(
            collection_name=state["collection_name"], offset=offset, limit=256,
            with_payload=True, with_vectors=False,
        )
        for point in page:
            payload = point.payload or {}
            source, chunk = payload.get("source"), payload.get("chunk")
            key = (source, chunk)
            if key not in expected or key in seen:
                raise ValueError(f"unexpected or duplicate point in staged snapshot: {source}#{chunk}")
            text = payload.get("text")
            metadata = payload.get("metadata") or {}
            actual = hashlib.sha256(str(text).encode("utf-8")).hexdigest()
            item = expected[key]
            if (payload.get("tenant_id") != state["tenant_id"]
                    or metadata.get("_rag_snapshot_id") != state["snapshot_id"]
                    or actual != item["content_sha256"]
                    or metadata.get("_rag_content_sha256") != actual
                    or metadata.get("_rag_source_sha256") != item["source_sha256"]):
                raise ValueError(f"stored point does not match frozen source manifest: {source}#{chunk}")
            seen.add(key)
        if offset is None:
            break
    if seen != set(expected):
        missing = sorted(set(expected) - seen)
        raise ValueError(f"staged snapshot point set differs from manifest; missing={missing[:5]}")
    if verify_sources and state.get("verify_sources"):
        is_current, changed = _snapshot_matches_root(
            state["snapshot_id"], _snapshot_source_root(state)
        )
        if not is_current:
            raise ValueError(f"source tree changed while snapshot was being built: {changed[:10]}")
    return len(seen)


def delete_tenant_collection(tenant_id: str, *, force: bool = False) -> None:
    """Best-effort deletion of the Qdrant collection backing a tenant.

    Normalizes the tenant id exactly like the engine does (the same
    `_collection` used by /rag/index and the retrieval adapters) and swallows
    failures — callers use this as a cleanup guard (test fixtures, experiment
    runners), never as a gate.

    Safety: a mis-typed tenant id must never wipe a real collection, so the
    guard refuses (raises) any tenant id that does not look like a test/eval
    tenant (`live_test_*` / `r02_eval_*`) — an unguarded cleanup once deleted
    the real wilson-vault collection mid-session. Real cleanup requires
    force=True (maintenance tooling with explicit operator intent).
    """
    if not force and not tenant_id.startswith(_TEST_TENANT_PREFIXES):
        raise ValueError(
            f"refusing to delete non-test tenant {tenant_id!r} "
            f"(expected a {_TEST_TENANT_PREFIXES!r} prefix); "
            "pass force=True for explicit real cleanup"
        )
    if not _HAS_QDRANT:
        return
    client = _qdrant_client()
    active = rag_index_state.active_snapshot(tenant_id)
    snapshot_collections = rag_index_state.snapshot_collections(tenant_id)
    if active and not force:
        raise ValueError(f"refusing to delete managed tenant {tenant_id!r} while an active snapshot exists")
    if active or snapshot_collections:
        collections = set(snapshot_collections)
        if active:
            collections.add(active["collection_name"])
        collections.add(_collection(tenant_id))
        failed = [name for name in sorted(collections) if not _delete_qdrant_collection(client, name)]
        if failed:
            raise RuntimeError(f"failed to remove test tenant collections: {failed}")
        rag_index_state.clear_tenant_state(tenant_id)
    else:
        try:
            client.delete_collection(collection_name=_collection(tenant_id))
        except Exception as exc:
            logger.debug("collection cleanup failed for %s: %s", tenant_id, exc)


_ID_NS = uuid.UUID("9c5c7c3e-6f1a-4b0e-9a2d-3d3e3f4f5f6f")


def _stable_point_id(source: str, chunk: int = 0) -> str:
    """Deterministic point ID for a (source, chunk) pair.

    Qdrant only accepts unsigned integers or UUIDs as point IDs. We derive a
    UUIDv5 from the source path + chunk index, so re-indexing a file replaces
    its own points (idempotent) instead of overwriting other files' points.
    The previous `id: idx` scheme collided across batches (every batch reused
    ids 0..14), silently capping the collection at BATCH_SIZE points no matter
    how many documents were submitted.
    """
    return str(uuid.uuid5(_ID_NS, f"{source}#{chunk}"))


async def _embed(text: str) -> list[float]:
    # nomic-embed-text has a 2048-token context; Ollama rejects longer prompts
    # with HTTP 500 ("input length exceeds the context length"). Truncate and
    # retry so a single long document can never fail the whole batch.
    last_exc: Exception | None = None
    while len(text) > 500:
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{_ollama_base()}/api/embeddings",
                    json={"model": _EMBED_MODEL, "prompt": text},
                )
                resp.raise_for_status()
                data = resp.json()
                vec = data.get("embedding") or []
                if len(vec) != _EMBED_DIM:
                    # Pad or truncate to expected dimension
                    vec = (vec + [0.0] * _EMBED_DIM)[:_EMBED_DIM]
                return vec
        except httpx.HTTPStatusError as exc:
            body = (exc.response.text or "").lower()
            if "context length" in body:
                text = text[: len(text) // 2]
                last_exc = exc
                continue
            raise
    # Final attempt at the minimum size; if that still fails, surface the
    # original context-length error so the failure isn't masked.
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{_ollama_base()}/api/embeddings",
                json={"model": _EMBED_MODEL, "prompt": text},
            )
            resp.raise_for_status()
            data = resp.json()
            vec = data.get("embedding") or []
            if len(vec) != _EMBED_DIM:
                vec = (vec + [0.0] * _EMBED_DIM)[:_EMBED_DIM]
            return vec
    except Exception as exc:
        raise RuntimeError(
            f"embedding failed after truncation retries for {len(text)} chars"
        ) from (last_exc or exc)


class IndexRequest(BaseModel):
    tenant_id: str
    documents: list[dict[str, Any]] = Field(default_factory=list)
    snapshot_id: str | None = None
    manifest: list[dict[str, Any]] | None = None


class SnapshotRequest(BaseModel):
    tenant_id: str
    snapshot_id: str
    source_count: int | None = None
    manifest_sha256: str | None = None


class SearchRequest(BaseModel):
    tenant_id: str
    query: str
    limit: int = 5


@router.post("/index")
async def rag_index(payload: IndexRequest) -> dict[str, Any]:
    if not _HAS_QDRANT:
        raise HTTPException(status_code=501, detail="Qdrant client not installed")

    tenant_id = payload.tenant_id
    client = _qdrant_client()
    snapshot_id = payload.snapshot_id
    snapshot = None
    if payload.manifest is not None:
        if snapshot_id is not None:
            raise HTTPException(status_code=400, detail="pass manifest or snapshot_id, not both")
        try:
            from msb_v3.core.config import settings

            if tenant_id == "wilson-vault" or tenant_id.startswith(_TEST_TENANT_PREFIXES):
                source_root = str(Path(settings.vault_path).expanduser().resolve())
                verify_sources = True
            else:
                source_root, verify_sources = None, False
            snapshot = rag_index_state.begin_snapshot(
                tenant_id, payload.manifest, verify_sources=verify_sources, source_root=source_root
            )
            snapshot_id = snapshot["snapshot_id"]
            client.create_collection(collection_name=snapshot["collection_name"],
                                     vectors_config={"size": _EMBED_DIM, "distance": "Cosine"})
        except (ValueError, TypeError) as exc:
            if snapshot_id is not None:
                try:
                    state = rag_index_state.abort_snapshot(snapshot_id, tenant_id)
                    if state:
                        _delete_qdrant_collection(client, state["collection_name"])
                except Exception as cleanup_exc:  # noqa: BLE001
                    logger.error("failed to clean up invalid RAG snapshot %s: %s", snapshot_id, cleanup_exc)
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            if snapshot_id is not None:
                try:
                    state = rag_index_state.abort_snapshot(snapshot_id, tenant_id)
                    if state:
                        _delete_qdrant_collection(client, state["collection_name"])
                except Exception as cleanup_exc:  # noqa: BLE001
                    logger.error("failed to clean up uncreated RAG snapshot %s: %s", snapshot_id, cleanup_exc)
            raise HTTPException(status_code=503, detail="could not create snapshot staging collection") from exc
    elif snapshot_id is not None:
        snapshot = rag_index_state.get_snapshot(snapshot_id, tenant_id)
        if not snapshot or snapshot["status"] != "building":
            raise HTTPException(status_code=409, detail="snapshot is missing or not writable")
    elif tenant_id == "wilson-vault" or rag_index_state.tenant_is_managed(tenant_id):
        raise HTTPException(status_code=409, detail="tenant requires staged snapshot indexing")
    else:
        _ensure_collection(client, _collection(tenant_id))

    collection = snapshot["collection_name"] if snapshot else _collection(tenant_id)
    documents = payload.documents
    if snapshot_id is not None:
        try:
            documents = rag_index_state.claim_snapshot_batch(snapshot_id, tenant_id, payload.documents)
        except (ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not documents:
            response = {"ok": True, "tenant_id": tenant_id, "snapshot_id": snapshot_id,
                        "collection": collection, "indexed": 0, "idempotent_retry": True}
            if snapshot:
                response.update({"manifest_sha256": snapshot["manifest_sha256"],
                                 "expected_points": snapshot["expected_points"],
                                 "source_count": snapshot["source_count"]})
            return response
    try:
        points: list[dict[str, Any]] = []
        for doc in documents:
            text = str(doc.get("text", ""))
            vector = await _embed(text)
            source = str(doc.get("source", ""))
            chunk = int(doc.get("chunk", 0))
            metadata = dict(doc.get("metadata") or {})
            if snapshot_id is not None:
                metadata.update({
                    "_rag_content_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    "_rag_source_sha256": doc["source_sha256"],
                    "_rag_snapshot_id": snapshot_id,
                })
            points.append({
                "id": _stable_point_id(source, chunk) if snapshot_id is not None
                      else str(doc.get("id") or _stable_point_id(source, chunk)),
                "vector": vector,
                "payload": {"tenant_id": tenant_id, "text": text, "source": source,
                            "chunk": chunk, "metadata": metadata},
            })
        if points:
            client.upsert(collection_name=collection, points=points)
        if snapshot_id is not None:
            rag_index_state.record_uploaded(snapshot_id, tenant_id, documents)
    except Exception:
        if snapshot_id is not None:
            rag_index_state.release_snapshot_batch(snapshot_id, documents)
        raise
    TRIUMVIRATE_HIPPOCAMPUS.labels(op="upsert").inc()
    result: dict[str, Any] = {"ok": True, "tenant_id": tenant_id,
                              "collection": collection, "indexed": len(points)}
    if snapshot_id is not None:
        assert snapshot is not None
        result.update({"snapshot_id": snapshot_id, "manifest_sha256": snapshot["manifest_sha256"],
                       "expected_points": snapshot["expected_points"],
                       "source_count": snapshot["source_count"]})
    return result


@router.post("/index/commit")
def rag_index_commit(payload: SnapshotRequest) -> dict[str, Any]:
    if not _HAS_QDRANT:
        raise HTTPException(status_code=501, detail="Qdrant client not installed")
    state = rag_index_state.get_snapshot(payload.snapshot_id, payload.tenant_id)
    if not state or state["status"] != "building":
        raise HTTPException(status_code=409, detail="snapshot is missing or not writable")
    try:
        count = _verify_snapshot_collection(_qdrant_client(), state, verify_sources=True)
        if payload.source_count is not None and payload.source_count != state["source_count"]:
            raise ValueError("source count does not match manifest")
        if payload.manifest_sha256 is not None and payload.manifest_sha256 != state["manifest_sha256"]:
            raise ValueError("manifest digest does not match staging snapshot")
        active, old_collection = rag_index_state.commit_snapshot(payload.snapshot_id, payload.tenant_id, count)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="snapshot verification or activation failed") from exc
    client = _qdrant_client()
    cleanup_ok = True
    if old_collection and old_collection != active["collection_name"]:
        # This collection was created and recorded by the snapshot manager.
        cleanup_ok = _delete_qdrant_collection(client, old_collection)
    # Preserve the legacy tenant collection on first migration: it may contain
    # operator-owned data and can be reviewed for explicit later cleanup.
    return {"ok": True, "tenant_id": payload.tenant_id, **active,
            "retired_collection_removed": cleanup_ok}


@router.post("/index/abort")
def rag_index_abort(payload: SnapshotRequest) -> dict[str, Any]:
    if not _HAS_QDRANT:
        raise HTTPException(status_code=501, detail="Qdrant client not installed")
    try:
        state = rag_index_state.abort_snapshot(payload.snapshot_id, payload.tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not state:
        return {"ok": True, "aborted": False, "already_absent": True}
    removed = _delete_qdrant_collection(_qdrant_client(), state["collection_name"])
    return {"ok": True, "aborted": True, "snapshot_id": payload.snapshot_id,
            "staging_collection_removed": removed}


@router.get("/index/status")
def rag_index_status(tenant_id: str) -> dict[str, Any]:
    if not _HAS_QDRANT:
        raise HTTPException(status_code=501, detail="Qdrant client not installed")
    if not rag_index_state.tenant_is_managed(tenant_id):
        client = _qdrant_client()
        try:
            exists = client.collection_exists(_collection(tenant_id))
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail="could not inspect legacy index") from exc
        if not exists:
            return {"tenant_id": tenant_id, "index_status": "LEGACY_UNVERIFIED",
                    "currentness": {"status": "unavailable", "reason": "no committed snapshot or legacy collection"},
                    "points_count": 0}
        try:
            points = int(getattr(client.get_collection(_collection(tenant_id)), "points_count", 0) or 0)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=503, detail="legacy index collection unavailable") from exc
        return {"tenant_id": tenant_id, "index_status": "LEGACY_UNVERIFIED",
                "currentness": {"status": "unavailable", "reason": "no source manifest"},
                "points_count": points}
    active = rag_index_state.active_snapshot(tenant_id)
    if not active or active.get("snapshot_status") != "active":
        return {"tenant_id": tenant_id, "index_status": "INTEGRITY_ERROR",
                "currentness": {"status": "unverified", "reason": "active snapshot pointer/state mismatch"},
                "points_count": None}
    try:
        points = _verify_snapshot_collection(_qdrant_client(), active, verify_sources=False)
    except ValueError as exc:
        return {"tenant_id": tenant_id, "index_status": "INTEGRITY_ERROR",
                "currentness": {"status": "unverified", "reason": str(exc)}, "points_count": None}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail="active snapshot collection unavailable") from exc
    current = True
    changed: list[str] = []
    if active.get("verify_sources"):
        try:
            current, changed = _snapshot_matches_root(active["snapshot_id"], _snapshot_source_root(active))
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail="could not verify configured source currentness") from exc
    current = current and points == active["expected_points"]
    if active.get("verify_sources"):
        status = "VERIFIED_CURRENT" if current else "STALE"
        currentness = "verified_against_source" if current else "stale"
    else:
        status, currentness = "SOURCE_CURRENTNESS_UNKNOWN", "unknown"
    return {"tenant_id": tenant_id, "index_status": status,
            "currentness": {"status": currentness, "snapshot_id": active["snapshot_id"],
                            "manifest_sha256": active["manifest_sha256"], "activated_at": active["activated_at"],
                            "expected_points": active["expected_points"], "points_count": points,
                            "source_count": active["source_count"], "changed_sources": changed[:50],
                            "changed_source_count": len(changed)}, "points_count": points}


@router.post("/search")
async def rag_search(payload: SearchRequest) -> dict[str, Any]:
    if not _HAS_QDRANT:
        raise HTTPException(status_code=501, detail="Qdrant client not installed")
    tenant_id = payload.tenant_id
    client = _qdrant_client()
    active = rag_index_state.active_snapshot(tenant_id)
    managed = rag_index_state.tenant_is_managed(tenant_id)
    if tenant_id == "wilson-vault" and not active:
        raise HTTPException(status_code=409, detail={
            "code": "legacy_unverified",
            "message": "The vault has no verified source snapshot; legacy retrieval is withheld until migration.",
        })
    if managed and not active:
        return {"ok": True, "tenant_id": tenant_id, "query": payload.query,
                "results": [], "index_status": "NOT_YET_PUBLISHED",
                "currentness": {"status": "unavailable", "reason": "no committed snapshot"}}
    collection = active["collection_name"] if active else _collection(tenant_id)
    try:
        client.get_collection(collection)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Active collection unavailable: {collection}") from exc
    if active and active.get("snapshot_status") != "active":
        raise HTTPException(status_code=503, detail="active snapshot pointer/state mismatch; results withheld")
    if active and not active.get("verify_sources"):
        raise HTTPException(status_code=409, detail={
            "code": "source_currentness_unknown",
            "message": "This snapshot has no configured source tree; authoritative search is withheld.",
            "snapshot_id": active["snapshot_id"],
        })
    if active and active.get("verify_sources"):
        try:
            is_current, changed = _snapshot_matches_root(active["snapshot_id"], _snapshot_source_root(active))
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail="could not verify configured source currentness; results withheld") from exc
        if not is_current:
            raise HTTPException(status_code=409, detail={
                "code": "index_stale",
                "message": "Vault differs from the active index snapshot; results withheld until a verified reindex.",
                "changed_sources": changed[:50], "changed_source_count": len(changed),
                "snapshot_id": active["snapshot_id"],
            })
    query_vector = await _embed(payload.query)
    results = client.query_points(collection_name=collection, query=query_vector,
                                  limit=payload.limit, with_payload=True)
    result_items = [{
        "score": float(point.score), "text": (point.payload or {}).get("text", ""),
        "source": (point.payload or {}).get("source", ""),
        "chunk": (point.payload or {}).get("chunk", 0),
        "metadata": (point.payload or {}).get("metadata", {}),
    } for point in results.points]
    if active:
        try:
            if active.get("snapshot_status") != "active":
                raise ValueError("active snapshot pointer/state mismatch")
            rag_index_state.validate_snapshot_hits(active["snapshot_id"], tenant_id, result_items)
            latest = rag_index_state.active_snapshot(tenant_id)
            if not latest or latest["snapshot_id"] != active["snapshot_id"]:
                raise ValueError("active snapshot changed while this search was running")
            if active.get("verify_sources"):
                try:
                    still_current, changed_after_query = _snapshot_matches_root(
                        active["snapshot_id"], _snapshot_source_root(active)
                    )
                except RuntimeError as exc:
                    raise HTTPException(status_code=503, detail="could not verify configured source currentness; results withheld") from exc
                if not still_current:
                    raise ValueError(f"source changed during search: {changed_after_query[:10]}")
        except ValueError as exc:
            logger.error("RAG currentness/provenance check refused results: %s", exc)
            raise HTTPException(status_code=503, detail="active snapshot integrity/currentness check failed; results withheld") from exc
    TRIUMVIRATE_HIPPOCAMPUS.labels(op="search").inc()
    response: dict[str, Any] = {"ok": True, "tenant_id": tenant_id,
                                "query": payload.query, "results": result_items}
    if active:
        response["index_status"] = (
            "VERIFIED_CURRENT" if active.get("verify_sources") else "SOURCE_CURRENTNESS_UNKNOWN"
        )
        response["currentness"] = {
            "status": "verified_against_source" if active.get("verify_sources") else "unknown",
            "snapshot_id": active["snapshot_id"],
            "manifest_sha256": active["manifest_sha256"],
            "activated_at": active["activated_at"],
            "expected_points": active["expected_points"],
            "source_count": active["source_count"],
        }
    else:
        response["index_status"] = "LEGACY_UNVERIFIED"
        response["currentness"] = {"status": "unavailable",
                                    "reason": "legacy in-place index has no committed source manifest"}
    return response
