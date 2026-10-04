"""Endpoint lifecycle tests for staged RAG snapshots; no external services."""
from __future__ import annotations

import hashlib
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from msb_v3.api import rag
from msb_v3.api.app import create_app
from msb_v3.core.config import settings


class FakeQdrant:
    def __init__(self):
        self.collections: dict[str, dict[str, dict]] = {}

    def create_collection(self, collection_name, vectors_config):
        assert vectors_config["size"] == rag._EMBED_DIM
        if collection_name in self.collections:
            raise RuntimeError("already exists")
        self.collections[collection_name] = {}

    def collection_exists(self, collection_name):
        return collection_name in self.collections

    def delete_collection(self, collection_name):
        self.collections.pop(collection_name, None)

    def get_collection(self, collection_name):
        if collection_name not in self.collections:
            raise RuntimeError("missing collection")
        return SimpleNamespace(points_count=len(self.collections[collection_name]))

    def upsert(self, collection_name, points):
        self.collections[collection_name].update({str(p["id"]): p["payload"] for p in points})

    def scroll(self, collection_name, offset=None, limit=256, **kwargs):
        points = list(self.collections[collection_name].values())
        start = int(offset or 0)
        page = [SimpleNamespace(payload=p) for p in points[start:start + limit]]
        next_offset = start + limit if start + limit < len(points) else None
        return page, next_offset

    def query_points(self, collection_name, query, limit, with_payload):
        points = list(self.collections[collection_name].values())[:limit]
        return SimpleNamespace(points=[SimpleNamespace(score=0.99, payload=p) for p in points])


def _manifest(root, texts):
    docs, manifest = [], []
    for source, text in texts.items():
        raw = (root / source).read_bytes()
        source_hash = hashlib.sha256(raw).hexdigest()
        chunks = rag._split_vault_text(text)
        for chunk, part in enumerate(chunks):
            doc = {"source": source, "chunk": chunk, "text": part,
                   "source_sha256": source_hash, "metadata": {"path": source}}
            docs.append(doc)
            manifest.append({"source": source, "chunk": chunk,
                             "content_sha256": hashlib.sha256(part.encode()).hexdigest(),
                             "source_sha256": source_hash})
    return manifest, docs


def _index(client, texts):
    from pathlib import Path

    root = Path(settings.vault_path)
    for source, text in texts.items():
        path = root / source
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    manifest, docs = _manifest(root, texts)
    opened = client.post("/rag/index", json={"tenant_id": "live_test_snapshot", "manifest": manifest})
    assert opened.status_code == 200, opened.text
    for offset in range(0, len(docs), 2):
        uploaded = client.post("/rag/index", json={"tenant_id": "live_test_snapshot",
                                "snapshot_id": opened.json()["snapshot_id"], "documents": docs[offset:offset + 2]})
        assert uploaded.status_code == 200, uploaded.text
    committed = client.post("/rag/index/commit", json={"tenant_id": "live_test_snapshot",
        "snapshot_id": opened.json()["snapshot_id"], "manifest_sha256": opened.json()["manifest_sha256"]})
    assert committed.status_code == 200, committed.text
    return committed.json()


@pytest.fixture
def api_env(monkeypatch, tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "state.db"))
    monkeypatch.setattr(settings, "vault_path", str(root))
    store = FakeQdrant()
    monkeypatch.setattr(rag, "_HAS_QDRANT", True)
    monkeypatch.setattr(rag, "_qdrant_client", lambda: store)

    async def fake_embed(_text):
        return [0.0] * rag._EMBED_DIM

    monkeypatch.setattr(rag, "_embed", fake_embed)
    return TestClient(create_app()), root, store


def test_snapshot_api_create_update_delete_recreate_and_withhold_stale(api_env):
    client, root, store = api_env
    tenant = "live_test_snapshot"
    _index(client, {"a.md": "ALPHA_MARKER original text", "b.md": "BETA_MARKER retained text"})

    status = client.get("/rag/index/status", params={"tenant_id": tenant})
    assert status.status_code == 200
    assert status.json()["index_status"] == "VERIFIED_CURRENT"
    result = client.post("/rag/search", json={"tenant_id": tenant, "query": "ALPHA_MARKER"})
    assert result.status_code == 200
    assert result.json()["results"][0]["text"] == "ALPHA_MARKER original text"
    assert result.json()["results"][0]["metadata"]["_rag_snapshot_id"] == result.json()["currentness"]["snapshot_id"]

    (root / "a.md").write_text("ALPHA_MARKER edited revision", encoding="utf-8")
    status = client.get("/rag/index/status", params={"tenant_id": tenant}).json()
    assert status["index_status"] == "STALE"
    assert "a.md" in status["currentness"]["changed_sources"]
    withheld = client.post("/rag/search", json={"tenant_id": tenant, "query": "ALPHA_MARKER"})
    assert withheld.status_code == 409
    assert withheld.json()["detail"]["code"] == "index_stale"

    updated = _index(client, {"a.md": "ALPHA_MARKER revised revision", "b.md": "BETA_MARKER retained text"})
    assert updated["retired_collection_removed"] is True
    assert updated["collection_name"] in store.collections
    assert len(store.collections) == 1
    result = client.post("/rag/search", json={"tenant_id": tenant, "query": "ALPHA_MARKER"}).json()
    assert "original text" not in result["results"][0]["text"]
    assert "revised revision" in result["results"][0]["text"]

    (root / "a.md").unlink()
    status = client.get("/rag/index/status", params={"tenant_id": tenant}).json()
    assert status["index_status"] == "STALE"
    withheld = client.post("/rag/search", json={"tenant_id": tenant, "query": "ALPHA_MARKER"})
    assert withheld.status_code == 409

    deleted = _index(client, {"b.md": "BETA_MARKER retained text"})
    assert deleted["snapshot_id"] != updated["snapshot_id"]
    result = client.post("/rag/search", json={"tenant_id": tenant, "query": "ALPHA_MARKER"}).json()
    assert all("ALPHA_MARKER" not in hit["text"] for hit in result["results"])
    assert client.get("/rag/index/status", params={"tenant_id": tenant}).json()["index_status"] == "VERIFIED_CURRENT"

    (root / "a.md").write_text("GAMMA_MARKER recreated source", encoding="utf-8")
    recreated = _index(client, {"a.md": "GAMMA_MARKER recreated source", "b.md": "BETA_MARKER retained text"})
    assert recreated["snapshot_id"] != deleted["snapshot_id"]
    result = client.post("/rag/search", json={"tenant_id": tenant, "query": "GAMMA_MARKER"}).json()
    assert "GAMMA_MARKER" in result["results"][0]["text"]
    assert "ALPHA_MARKER" not in result["results"][0]["text"]
    assert len(store.collections) == 1
    client.close()


def test_empty_manifest_and_incomplete_build_cannot_replace_active(api_env):
    client, root, store = api_env
    tenant = "live_test_snapshot"
    active = _index(client, {"keep.md": "KEEP_MARKER stable source"})
    empty = client.post("/rag/index", json={"tenant_id": tenant, "manifest": []})
    assert empty.status_code == 400
    (root / "new.md").write_text("NEW_MARKER candidate", encoding="utf-8")
    manifest, _docs = _manifest(root, {"new.md": "NEW_MARKER candidate"})
    opened = client.post("/rag/index", json={"tenant_id": tenant, "manifest": manifest}).json()
    incomplete = client.post("/rag/index/commit", json={"tenant_id": tenant, "snapshot_id": opened["snapshot_id"]})
    assert incomplete.status_code == 409
    assert client.get("/rag/index/status", params={"tenant_id": tenant}).json()["currentness"]["snapshot_id"] == active["snapshot_id"]
    aborted = client.post("/rag/index/abort", json={"tenant_id": tenant, "snapshot_id": opened["snapshot_id"]})
    assert aborted.status_code == 200
    assert aborted.json()["staging_collection_removed"] is True
    assert len(store.collections) == 1
    client.close()


def test_managed_tenant_rejects_legacy_in_place_update(api_env):
    client, _root, _store = api_env
    _index(client, {"a.md": "A managed source"})
    response = client.post("/rag/index", json={"tenant_id": "live_test_snapshot",
                              "documents": [{"source": "b.md", "text": "unsafe in-place"}]})
    assert response.status_code == 409
    client.close()


def test_client_supplied_hash_cannot_publish_different_source(api_env):
    client, root, _store = api_env
    tenant = "live_test_snapshot"
    original = _index(client, {"keep.md": "KEEP_MARKER stable source"})
    (root / "keep.md").write_text("KEEP_MARKER stable source", encoding="utf-8")
    (root / "forged.md").write_text("ACTUAL_SOURCE", encoding="utf-8")
    forged_text = "NOT_THE_SOURCE"
    forged_manifest = [{
        "source": "forged.md", "chunk": 0,
        "content_sha256": hashlib.sha256(forged_text.encode()).hexdigest(),
        "source_sha256": hashlib.sha256(b"ACTUAL_SOURCE").hexdigest(),
    }]
    opened = client.post("/rag/index", json={"tenant_id": tenant, "manifest": forged_manifest}).json()
    uploaded = client.post("/rag/index", json={"tenant_id": tenant,
        "snapshot_id": opened["snapshot_id"], "documents": [{
            "source": "forged.md", "chunk": 0, "text": forged_text,
            "source_sha256": forged_manifest[0]["source_sha256"],
        }]})
    assert uploaded.status_code == 200
    commit = client.post("/rag/index/commit", json={"tenant_id": tenant,
        "snapshot_id": opened["snapshot_id"]})
    assert commit.status_code == 409
    status = client.get("/rag/index/status", params={"tenant_id": tenant}).json()
    assert status["index_status"] == "STALE"
    assert status["currentness"]["snapshot_id"] == original["snapshot_id"]
    client.close()


def test_qdrant_payload_tampering_surfaces_integrity_error_and_withholds_search(api_env):
    client, _root, store = api_env
    tenant = "live_test_snapshot"
    active = _index(client, {"a.md": "TRUSTED_MARKER exact content"})
    collection = store.collections[active["collection_name"]]
    next(iter(collection.values()))["text"] = "TAMPERED_MARKER forged content"
    status = client.get("/rag/index/status", params={"tenant_id": tenant})
    assert status.status_code == 200
    assert status.json()["index_status"] == "INTEGRITY_ERROR"
    search = client.post("/rag/search", json={"tenant_id": tenant, "query": "TRUSTED_MARKER"})
    assert search.status_code == 503
    assert "withheld" in search.json()["detail"]
    client.close()


def test_production_vault_refuses_legacy_in_place_index(api_env):
    client, _root, _store = api_env
    response = client.post("/rag/index", json={"tenant_id": "wilson-vault",
        "documents": [{"source": "old.md", "text": "legacy mutation"}]})
    assert response.status_code == 409
    assert "staged snapshot" in response.json()["detail"]
    client.close()


def test_manifest_only_tenant_is_never_labeled_source_current(api_env):
    client, _root, _store = api_env
    tenant = "unmanaged_snapshot"
    manifest = [{"source": "external.md", "chunk": 0,
        "content_sha256": hashlib.sha256(b"external source").hexdigest(),
        "source_sha256": hashlib.sha256(b"external source").hexdigest()}]
    opened = client.post("/rag/index", json={"tenant_id": tenant, "manifest": manifest})
    assert opened.status_code == 200
    upload = client.post("/rag/index", json={"tenant_id": tenant,
        "snapshot_id": opened.json()["snapshot_id"], "documents": [{
            "source": "external.md", "chunk": 0, "text": "external source",
            "source_sha256": manifest[0]["source_sha256"],
        }]})
    assert upload.status_code == 200
    commit = client.post("/rag/index/commit", json={"tenant_id": tenant,
        "snapshot_id": opened.json()["snapshot_id"]})
    assert commit.status_code == 200
    status = client.get("/rag/index/status", params={"tenant_id": tenant}).json()
    assert status["index_status"] == "SOURCE_CURRENTNESS_UNKNOWN"
    assert status["currentness"]["status"] == "unknown"
    withheld = client.post("/rag/search", json={"tenant_id": tenant, "query": "external source"})
    assert withheld.status_code == 409
    assert withheld.json()["detail"]["code"] == "source_currentness_unknown"
    client.close()


@pytest.mark.live
def test_live_snapshot_lifecycle_with_real_qdrant_and_ollama(monkeypatch, tmp_path):
    """Exercise API endpoints against real Qdrant/Ollama using only a temp source tree/DB."""
    import httpx

    from msb_v3.api import rag_index_state

    try:
        qdrant = rag._qdrant_client()
        qdrant.get_collections()
        tags = httpx.get(f"{rag._ollama_base()}/api/tags", timeout=3)
        tags.raise_for_status()
        available = {item["name"].split(":")[0] for item in tags.json().get("models", [])}
        if rag._EMBED_MODEL.split(":")[0] not in available:
            pytest.skip(f"configured embedding model {rag._EMBED_MODEL!r} is unavailable")
    except Exception as exc:
        pytest.skip(f"live Qdrant/Ollama unavailable: {exc}")

    tenant = f"live_test_snapshot_{uuid.uuid4().hex[:12]}"
    root = tmp_path / "source"
    root.mkdir()
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "state.db"))
    monkeypatch.setattr(settings, "vault_path", str(root))
    monkeypatch.setattr(rag, "_qdrant_client", lambda: qdrant)
    client = TestClient(create_app())

    def index_files(texts: dict[str, str]) -> dict:
        docs, manifest = [], []
        for source, text in texts.items():
            path = root / source
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            raw = path.read_bytes()
            source_hash = hashlib.sha256(raw).hexdigest()
            for chunk, part in enumerate(rag._split_vault_text(text)):
                docs.append({"source": source, "chunk": chunk, "text": part,
                             "source_sha256": source_hash, "metadata": {"path": source}})
                manifest.append({"source": source, "chunk": chunk,
                                 "content_sha256": hashlib.sha256(part.encode()).hexdigest(),
                                 "source_sha256": source_hash})
        opened_response = client.post("/rag/index", json={"tenant_id": tenant, "manifest": manifest})
        assert opened_response.status_code == 200, opened_response.text
        opened = opened_response.json()
        for offset in range(0, len(docs), 2):
            uploaded = client.post("/rag/index", json={"tenant_id": tenant,
                "snapshot_id": opened["snapshot_id"], "documents": docs[offset:offset + 2]})
            assert uploaded.status_code == 200, uploaded.text
        committed = client.post("/rag/index/commit", json={"tenant_id": tenant,
            "snapshot_id": opened["snapshot_id"], "manifest_sha256": opened["manifest_sha256"]})
        assert committed.status_code == 200, committed.text
        return committed.json()

    try:
        suffix = uuid.uuid4().hex[:8]
        marker_a, marker_b, marker_c = (f"MARKER_{name}_{suffix}" for name in ("A", "B", "C"))
        first = index_files({"a.md": f"{marker_a} original source revision.",
                             "b.md": f"{marker_b} retained source."})
        status = client.get("/rag/index/status", params={"tenant_id": tenant}).json()
        assert status["index_status"] == "VERIFIED_CURRENT"
        assert status["currentness"]["snapshot_id"] == first["snapshot_id"]
        initial = client.post("/rag/search", json={"tenant_id": tenant, "query": marker_a, "limit": 10})
        assert initial.status_code == 200, initial.text
        assert any(hit["text"] == f"{marker_a} original source revision." for hit in initial.json()["results"])
        assert all(hit["metadata"].get("_rag_snapshot_id") == first["snapshot_id"] for hit in initial.json()["results"])

        frozen, _ = _manifest(root, {"a.md": f"{marker_a} original source revision."})
        staged = client.post("/rag/index", json={"tenant_id": tenant, "manifest": frozen}).json()
        incomplete = client.post("/rag/index/commit", json={"tenant_id": tenant,
            "snapshot_id": staged["snapshot_id"]})
        assert incomplete.status_code == 409
        assert client.post("/rag/index/abort", json={"tenant_id": tenant,
            "snapshot_id": staged["snapshot_id"]}).status_code == 200
        assert client.get("/rag/index/status", params={"tenant_id": tenant}).json()["currentness"]["snapshot_id"] == first["snapshot_id"]

        (root / "a.md").write_text(f"{marker_a} edited source revision.", encoding="utf-8")
        stale = client.get("/rag/index/status", params={"tenant_id": tenant}).json()
        assert stale["index_status"] == "STALE"
        assert "a.md" in stale["currentness"]["changed_sources"]
        withheld = client.post("/rag/search", json={"tenant_id": tenant, "query": marker_a})
        assert withheld.status_code == 409

        updated = index_files({"a.md": f"{marker_a} edited source revision.",
                               "b.md": f"{marker_b} retained source."})
        found = client.post("/rag/search", json={"tenant_id": tenant, "query": marker_a, "limit": 10})
        assert found.status_code == 200
        bodies = [hit["text"] for hit in found.json()["results"]]
        assert any("edited source revision" in body for body in bodies)
        assert all("original source revision" not in body for body in bodies)
        assert updated["snapshot_id"] != first["snapshot_id"]

        (root / "a.md").unlink()
        assert client.get("/rag/index/status", params={"tenant_id": tenant}).json()["index_status"] == "STALE"
        assert client.post("/rag/search", json={"tenant_id": tenant, "query": marker_a}).status_code == 409
        deleted = index_files({"b.md": f"{marker_b} retained source."})
        deleted_hits = client.post("/rag/search", json={"tenant_id": tenant,
            "query": marker_a, "limit": 10})
        assert deleted_hits.status_code == 200
        assert all(marker_a not in hit["text"] for hit in deleted_hits.json()["results"])
        assert client.get("/rag/index/status", params={"tenant_id": tenant}).json()["index_status"] == "VERIFIED_CURRENT"

        recreated = index_files({"a.md": f"{marker_c} recreated source.",
                                 "b.md": f"{marker_b} retained source."})
        recreated_hits = client.post("/rag/search", json={"tenant_id": tenant,
            "query": marker_c, "limit": 10})
        assert recreated_hits.status_code == 200
        assert any(marker_c in hit["text"] for hit in recreated_hits.json()["results"])
        assert all(marker_a not in hit["text"] for hit in recreated_hits.json()["results"])
        assert recreated["snapshot_id"] != deleted["snapshot_id"]
        assert client.get("/rag/index/status", params={"tenant_id": tenant}).json()["index_status"] == "VERIFIED_CURRENT"
    finally:
        collections = rag_index_state.snapshot_collections(tenant)
        active = rag_index_state.active_snapshot(tenant)
        if active:
            collections.append(active["collection_name"])
        collections.append(rag._collection(tenant))
        rag.delete_tenant_collection(tenant, force=True)
        assert all(not qdrant.collection_exists(name) for name in set(collections))
        assert not rag_index_state.tenant_is_managed(tenant)
        assert not rag_index_state.snapshot_collections(tenant)
