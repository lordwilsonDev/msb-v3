"""Phase 1.2b — hippocampus endpoints go through the VectorStore interface.

The /triumvirate/hippocampus/{upsert,search} endpoints were re-wired from
VectorHippocampus (a bespoke SQLite store) onto the unified
retrieval.vector_store.VectorStore contract. This proves the HTTP contract is
preserved end-to-end: the same request shapes still work, and the
(doc_id, chunk_id) identity round-trips through the backend-agnostic
VectorDocument/VectorHit shapes.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from msb_v3.api.triumvirate import router as triumvirate_router  # noqa: E402
from msb_v3.core.container import build_container  # noqa: E402
from msb_v3.retrieval.vector_store import SQLiteVectorStore  # noqa: E402


@pytest.fixture
def client(tmp_path):
    """Mount the triumvirate router over an isolated tmp SQLite backend,
    injected through the ApplicationContainer (Phase 1.4) rather than by
    monkeypatching a module-level singleton."""
    store = SQLiteVectorStore(db_path=tmp_path / "hippocampus.db", tenant_id="default")
    app = FastAPI()
    app.state.container = build_container(hippocampus=store)
    app.include_router(triumvirate_router, prefix="/triumvirate")
    return TestClient(app)


def test_hippocampus_upsert_contract(client):
    resp = client.post(
        "/triumvirate/hippocampus/upsert",
        json={"doc_id": "doc1", "chunk_id": "c1", "text": "alpha", "embedding": [1.0, 0.0]},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "doc_id": "doc1", "chunk_id": "c1"}


def test_hippocampus_search_round_trips_identity(client):
    client.post(
        "/triumvirate/hippocampus/upsert",
        json={
            "doc_id": "doc1",
            "chunk_id": "c1",
            "text": "alpha",
            "embedding": [1.0, 0.0],
            "metadata": {"tag": "x"},
        },
    )
    resp = client.post(
        "/triumvirate/hippocampus/search",
        json={"embedding": [1.0, 0.0], "limit": 5},
    )
    assert resp.status_code == 200
    results = resp.json()["results"]
    assert len(results) == 1
    hit = results[0]
    assert hit["doc_id"] == "doc1"
    assert hit["chunk_id"] == "c1"
    assert hit["text"] == "alpha"
    assert hit["metadata"] == {"tag": "x"}
    assert 0.99 <= hit["score"] <= 1.0


def test_hippocampus_search_ranks_and_isolates_chunks(client):
    client.post(
        "/triumvirate/hippocampus/upsert",
        json={"doc_id": "doc1", "chunk_id": "c1", "text": "alpha", "embedding": [1.0, 0.0]},
    )
    client.post(
        "/triumvirate/hippocampus/upsert",
        json={"doc_id": "doc1", "chunk_id": "c2", "text": "beta", "embedding": [0.0, 1.0]},
    )
    resp = client.post(
        "/triumvirate/hippocampus/search",
        json={"embedding": [1.0, 0.0], "limit": 2},
    )
    results = resp.json()["results"]
    assert [h["chunk_id"] for h in results] == ["c1", "c2"]
    assert results[0]["score"] > results[1]["score"]
