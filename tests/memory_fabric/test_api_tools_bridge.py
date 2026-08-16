"""Memory Fabric surface tests — operator-gated API, governed tools
(capability gate on memory.store), and MCP bridge exposure."""

import pytest
from fastapi.testclient import TestClient

from msb_v3.api import mcp_bridge
from msb_v3.api.app import create_app
from msb_v3.core.config import settings
from msb_v3.tools import executors, runtime
from msb_v3.tools.registry import TOOLS

SECRET = "secret-token"


@pytest.fixture()
def api_client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "operator_token", "test-operator-token")
    monkeypatch.setattr(settings, "memory_fabric_db_path", str(tmp_path / "memory.db"))
    return TestClient(create_app(), headers={"Authorization": "Bearer test-operator-token"})


@pytest.fixture()
def bridge_client(tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_bridge, "_MCP_BRIDGE_SECRET", SECRET, raising=False)
    monkeypatch.setattr(settings, "memory_fabric_db_path", str(tmp_path / "memory.db"))
    return TestClient(create_app())


def _post(client, payload):
    return client.post("/mcp/proxy", json=payload, headers={"x-mcp-secret": SECRET})


# --- API -------------------------------------------------------------------


def test_api_store_and_recall(api_client):
    r = api_client.post("/memory-fabric/store", json={"content": "the port is 8766", "tags": ["api"], "project": "msb"})
    assert r.status_code == 200
    mid = r.json()["memory"]["memory_id"]
    r2 = api_client.get("/memory-fabric/recall", params={"query": "port"})
    assert r2.status_code == 200
    assert any(m["memory_id"] == mid for m in r2.json()["memories"])


def test_api_requires_operator(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "operator_token", "test-operator-token")
    no_auth = TestClient(create_app())
    assert no_auth.get("/memory-fabric/recall", params={"query": "x"}).status_code == 401
    assert no_auth.post("/memory-fabric/store", json={"content": "x"}).status_code == 401


def test_api_verify_and_history(api_client):
    mid = api_client.post("/memory-fabric/store", json={"content": "fact"}).json()["memory"]["memory_id"]
    r = api_client.post("/memory-fabric/verify", json={"memory_id": mid, "to_state": "VERIFIED", "by": "tester"})
    assert r.status_code == 200
    assert r.json()["memory"]["verification_state"] == "VERIFIED"
    hist = api_client.get(f"/memory-fabric/{mid}").json()["verification_history"]
    assert len(hist) == 1 and hist[0]["by"] == "tester"


def test_api_illegal_verify_422(api_client):
    mid = api_client.post("/memory-fabric/store", json={"content": "fact"}).json()["memory"]["memory_id"]
    api_client.post("/memory-fabric/verify", json={"memory_id": mid, "to_state": "VERIFIED"})
    r = api_client.post("/memory-fabric/verify", json={"memory_id": mid, "to_state": "UNVERIFIED"})
    assert r.status_code == 422


def test_api_forget_and_stats(api_client):
    mid = api_client.post("/memory-fabric/store", json={"content": "gone soon"}).json()["memory"]["memory_id"]
    assert api_client.post("/memory-fabric/forget", json={"memory_id": mid}).status_code == 200
    stats = api_client.get("/memory-fabric/stats").json()["stats"]
    assert stats["archived"] == 1
    assert stats["active"] == 0


def test_api_consolidate(api_client):
    for i in range(2):
        api_client.post("/memory-fabric/store", json={"content": "duplicate fact", "tags": ["dup"], "project": "p"})
    r = api_client.post("/memory-fabric/consolidate", json={"tenant": "default"})
    assert r.status_code == 200
    assert r.json()["consolidation"]["merged"] == 1


def test_api_unknown_memory_404(api_client):
    assert api_client.get("/memory-fabric/nope").status_code == 404


# --- governed tools ---------------------------------------------------------


def test_registry_has_memory_tools():
    assert "memory.recall" in TOOLS
    assert "memory.store" in TOOLS
    assert TOOLS["memory.recall"].risk_class == "LOW"
    assert TOOLS["memory.recall"].mutation_class == "NONE"
    assert TOOLS["memory.store"].risk_class == "MEDIUM"
    assert TOOLS["memory.store"].mutation_class == "WRITE"
    assert TOOLS["memory.store"].required_capabilities == ("memory.write",)
    # executors exist under underscore names
    assert callable(executors.memory_recall)
    assert callable(executors.memory_store)


def test_memory_store_denied_without_capability(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "memory_fabric_db_path", str(tmp_path / "memory.db"))

    class Client:
        registered = {}

        def register_tool(self, name, fn):
            self.registered[name] = fn

    c = Client()
    runtime.register_governed_tools(c, {"tools": [{"name": "memory.store"}], "session": "s"})
    out = c.registered["memory.store"](content="x")
    assert out.startswith("[denied]")
    assert "memory.write" in out


def test_memory_store_allowed_with_capability(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "memory_fabric_db_path", str(tmp_path / "memory.db"))

    class Client:
        registered = {}

        def register_tool(self, name, fn):
            self.registered[name] = fn

    c = Client()
    runtime.register_governed_tools(
        c, {"tools": [{"name": "memory.store"}], "granted_capabilities": ["memory.write"], "session": "s"}
    )
    out = c.registered["memory.store"](content="remember this", tags=["t"])
    assert out.startswith("stored ")
    assert "UNVERIFIED" in out


def test_memory_recall_executor(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "memory_fabric_db_path", str(tmp_path / "memory.db"))
    from msb_v3.memory_fabric.fabric import MemoryFabric
    from msb_v3.memory_fabric.store import MemoryFabricStore

    MemoryFabric(MemoryFabricStore(str(tmp_path / "memory.db"))).store_memory("sovereign memory works", tags=["sovereign"])
    out = executors.memory_recall({"query": "sovereign"}, tenant="default", session="s")
    assert "sovereign memory works" in out


# --- MCP bridge --------------------------------------------------------------


def test_manifest_has_memory_tools(bridge_client):
    names = {t["name"] for t in mcp_bridge._MCP_TOOLS}
    assert {"memory_store", "memory_recall", "memory_verify", "memory_forget", "memory_consolidate"} <= names


def test_bridge_store_recall_verify(bridge_client):
    r = _post(bridge_client, {"tool": "memory_store", "args": {"content": "bridge memory", "tags": ["bridge"]}})
    assert r.status_code == 200
    mid = r.json()["result"]["memory_id"]
    r2 = _post(bridge_client, {"tool": "memory_recall", "args": {"query": "bridge"}})
    assert r2.status_code == 200
    assert r2.json()["result"]["count"] == 1
    r3 = _post(bridge_client, {"tool": "memory_verify", "args": {"memory_id": mid, "to_state": "VERIFIED"}})
    assert r3.status_code == 200
    assert r3.json()["result"]["verification_state"] == "VERIFIED"


def test_bridge_forget_removes_from_recall(bridge_client):
    mid = _post(bridge_client, {"tool": "memory_store", "args": {"content": "will forget"}}).json()["result"]["memory_id"]
    assert _post(bridge_client, {"tool": "memory_forget", "args": {"memory_id": mid}}).status_code == 200
    r = _post(bridge_client, {"tool": "memory_recall", "args": {"query": "will forget"}})
    assert r.json()["result"]["count"] == 0


def test_bridge_consolidate(bridge_client):
    for i in range(2):
        _post(bridge_client, {"tool": "memory_store", "args": {"content": "dup", "tags": ["dup"], "project": "p"}})
    r = _post(bridge_client, {"tool": "memory_consolidate", "args": {"tenant": "default"}})
    assert r.json()["result"]["merged"] == 1


def test_bridge_requires_secret(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "memory_fabric_db_path", str(tmp_path / "memory.db"))
    no_secret = TestClient(create_app())
    assert no_secret.post("/mcp/proxy", json={"tool": "memory_recall", "args": {"query": "x"}}).status_code == 401
