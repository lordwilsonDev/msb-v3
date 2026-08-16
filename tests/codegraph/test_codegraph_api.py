"""API tests — operator gating + the /codegraph query surface."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from msb_v3.api.app import create_app
from msb_v3.core.config import settings

# Index the fixtures PARENT so rel paths include sample_repo/.
FIXTURES = Path(__file__).parent / "fixtures"
REPO = str(FIXTURES)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "operator_token", "test-operator-token")
    monkeypatch.setattr(settings, "codegraph_db_path", str(tmp_path / "graph.db"))
    # index the fixture before any query
    from msb_v3.codegraph.indexer import CodeGraphIndexer
    from msb_v3.codegraph.store import CodeGraphStore

    CodeGraphIndexer(CodeGraphStore(settings.codegraph_db_path)).index(REPO)
    return TestClient(create_app(), headers={"Authorization": "Bearer test-operator-token"})


def test_index_requires_operator(monkeypatch):
    from msb_v3.core.config import settings as s

    # monkeypatch, not direct assignment: a bare `s.operator_token = ...` leaks
    # into every later test (settings is a module singleton), which silently
    # turns on the production repair() auth gate for unrelated tests.
    monkeypatch.setattr(s, "operator_token", "test-operator-token")
    no_auth = TestClient(create_app())
    assert no_auth.post("/codegraph/index", json={"path": REPO}).status_code == 401


def test_index_endpoint(client):
    resp = client.post("/codegraph/index", json={"path": REPO})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"]
    assert body["nodes"] > 0


def test_index_validates_path(client):
    resp = client.post("/codegraph/index", json={"path": "  "})
    assert resp.status_code == 422


def test_stats_endpoint(client):
    resp = client.get(f"/codegraph/{REPO}/stats")
    assert resp.status_code == 200
    stats = resp.json()["stats"]
    assert stats["nodes"] > 0


def test_symbol_search_endpoint(client):
    resp = client.get(f"/codegraph/{REPO}/symbol", params={"name": "compute"})
    assert resp.status_code == 200
    assert any(s["name"] == "compute" for s in resp.json()["symbols"])


def test_callers_endpoint(client):
    resp = client.get(
        f"/codegraph/{REPO}/callers", params={"symbol": "sample_repo.engine.compute"}
    )
    assert resp.status_code == 200
    sources = {c["source"] for c in resp.json()["callers"]}
    assert "sample_repo.main.main" in sources


def test_callees_endpoint(client):
    resp = client.get(
        f"/codegraph/{REPO}/callees", params={"symbol": "sample_repo.engine.compute"}
    )
    assert resp.status_code == 200
    targets = {c["target"] for c in resp.json()["callees"]}
    assert "sample_repo.utils.normalize" in targets


def test_impact_endpoint(client):
    resp = client.get(
        f"/codegraph/{REPO}/impact", params={"file": "sample_repo/engine.py"}
    )
    assert resp.status_code == 200
    assert resp.json()["impact"]["seeds"]


def test_context_endpoint(client):
    resp = client.get(
        f"/codegraph/{REPO}/context", params={"symbol": "sample_repo.engine.compute"}
    )
    assert resp.status_code == 200
    ctx = resp.json()["context"]
    assert ctx["found"] and ctx["kind"] == "function"


def test_rename_endpoint(client):
    resp = client.get(f"/codegraph/{REPO}/rename", params={"name": "compute"})
    assert resp.status_code == 200
    assert resp.json()["rename"]["reference_count"] > 0


def test_unknown_repo_queries_are_honest(client):
    resp = client.get("/codegraph/nope/symbol", params={"name": "x"})
    assert resp.status_code == 200
    assert resp.json()["symbols"] == []  # honest empty, not an error


def test_ts_fixture_parsed_approximate(client):
    resp = client.get(f"/codegraph/{REPO}/symbol", params={"name": "Greeter"})
    assert resp.status_code == 200
    hits = [s for s in resp.json()["symbols"] if s["name"] == "Greeter"]
    assert hits and hits[0]["approximate"] == 1
