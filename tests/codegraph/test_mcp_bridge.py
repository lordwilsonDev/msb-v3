"""MCP bridge — the five codegraph tools are discoverable and queryable
through /mcp/proxy with the same x-mcp-secret gate as every other bridge
tool. Queries run in-process against the local SQLite graph (containment:
no source-tree access, no network hop). Indexing stays operator-gated at
POST /codegraph/index — these are read-only queries only.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from msb_v3.api import mcp_bridge
from msb_v3.api.app import create_app

FIXTURES = Path(__file__).parent / "fixtures"
REPO = str(FIXTURES)
SECRET = "secret-token"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MCP_BRIDGE_SECRET", SECRET)
    monkeypatch.setattr(mcp_bridge, "_MCP_BRIDGE_SECRET", SECRET, raising=False)
    monkeypatch.setattr(mcp_bridge, "settings", mcp_bridge.settings, raising=False)
    monkeypatch.setattr(
        mcp_bridge.settings, "codegraph_db_path", str(tmp_path / "graph.db")
    )
    from msb_v3.codegraph.indexer import CodeGraphIndexer
    from msb_v3.codegraph.store import CodeGraphStore

    CodeGraphIndexer(CodeGraphStore(str(tmp_path / "graph.db"))).index(REPO)
    return TestClient(create_app())


def _post(client: TestClient, payload: dict):
    return client.post("/mcp/proxy", json=payload, headers={"x-mcp-secret": SECRET})


def test_manifest_includes_codegraph_tools(client):
    names = {t["name"] for t in mcp_bridge._MCP_TOOLS}
    assert {"codegraph_stats", "codegraph_explore", "codegraph_context", "codegraph_impact", "codegraph_rename"} <= names


def test_codegraph_explore_requires_auth(client):
    resp = client.post("/mcp/proxy", json={"tool": "codegraph_explore", "args": {"repo": REPO, "name": "Engine"}})
    assert resp.status_code == 401


def test_codegraph_explore(client):
    resp = _post(client, {"tool": "codegraph_explore", "args": {"repo": REPO, "name": "Engine"}})
    assert resp.status_code == 200
    symbols = resp.json()["result"]["symbols"]
    assert symbols and symbols[0]["kind"] == "class"
    assert symbols[0]["name"] == "Engine"


def test_codegraph_context(client):
    resp = _post(client, {"tool": "codegraph_context", "args": {"repo": REPO, "symbol": "sample_repo.engine.compute"}})
    assert resp.status_code == 200
    ctx = resp.json()["result"]
    assert ctx["found"]
    assert ctx["kind"] == "function"
    caller_syms = {c["symbol"] for c in ctx["callers"]}
    assert "sample_repo.main.main" in caller_syms


def test_codegraph_impact(client):
    resp = _post(client, {"tool": "codegraph_impact", "args": {"repo": REPO, "file": "sample_repo/engine.py"}})
    assert resp.status_code == 200
    impact = resp.json()["result"]
    assert impact["seeds"]
    dep_syms = {d["symbol"] for d in impact["dependents"]}
    assert "sample_repo.main.main" in dep_syms


def test_codegraph_rename(client):
    resp = _post(client, {"tool": "codegraph_rename", "args": {"repo": REPO, "name": "compute"}})
    assert resp.status_code == 200
    rename = resp.json()["result"]
    assert rename["definitions"]
    assert rename["reference_count"] > 0


def test_codegraph_stats(client):
    resp = _post(client, {"tool": "codegraph_stats", "args": {"repo": REPO}})
    assert resp.status_code == 200
    stats = resp.json()["result"]
    assert stats["nodes"] > 0
    assert stats["edges"] > 0


def test_codegraph_requires_repo_arg(client):
    resp = _post(client, {"tool": "codegraph_explore", "args": {"name": "Engine"}})
    assert resp.status_code == 400


def test_codegraph_unknown_repo_is_honest(client):
    resp = _post(client, {"tool": "codegraph_explore", "args": {"repo": "/nope", "name": "x"}})
    assert resp.status_code == 200
    assert resp.json()["result"]["symbols"] == []  # honest empty, not an error
