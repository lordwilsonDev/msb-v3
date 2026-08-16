"""Software Factory surface tests — governed tool, operator-gated API, MCP
bridge. The pipeline semantics live in test_factory_pipeline.py; these pin
the wiring."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from msb_v3.api import mcp_bridge
from msb_v3.api.app import create_app
from msb_v3.core.config import settings
from msb_v3.tools import executors, runtime
from msb_v3.tools.registry import TOOLS

SECRET = "secret-token"


@pytest.fixture()
def api_client(monkeypatch):
    monkeypatch.setattr(settings, "operator_token", "test-operator-token")
    return TestClient(create_app(), headers={"Authorization": "Bearer test-operator-token"})


@pytest.fixture()
def bridge_client(monkeypatch):
    monkeypatch.setenv("MCP_BRIDGE_SECRET", SECRET)
    monkeypatch.setattr(mcp_bridge, "_MCP_BRIDGE_SECRET", SECRET, raising=False)
    return TestClient(create_app())


def _post(client, payload):
    return client.post("/mcp/proxy", json=payload, headers={"x-mcp-secret": SECRET})


# --- governed tool ---------------------------------------------------------


def test_registry_has_factory_run():
    assert "factory.run" in TOOLS
    td = TOOLS["factory.run"]
    assert td.risk_class == "MEDIUM"
    assert td.mutation_class == "WRITE"
    assert td.required_capabilities == ("factory.run",)
    assert callable(executors.factory_run)


def test_factory_run_executor_requires_args():
    out = executors.factory_run({}, tenant="t", session="s")
    assert out.startswith("[tool-error]")
    out = executors.factory_run({"title": "x", "repo": "/no/such/dir"}, tenant="t", session="s")
    assert "not a directory" in out


def test_factory_run_executor_unknown_builder(repo):
    out = executors.factory_run({"title": "x", "repo": str(repo), "builder": "alien"}, tenant="t", session="s")
    assert "unknown builder" in out


def test_runtime_gate_registers_factory_tool():
    class Client:
        registered = {}

        def register_tool(self, name, fn):
            self.registered[name] = fn

    c = Client()
    runtime.register_governed_tools(
        c,
        {"tools": [{"name": "factory.run"}], "session": "s", "granted_capabilities": ["factory.run"]},
    )
    assert "factory.run" in c.registered
    out = c.registered["factory.run"](title="x", repo="/no/such/dir")
    assert "not a directory" in out


def test_runtime_gate_denies_factory_without_capability():
    class Client:
        registered = {}

        def register_tool(self, name, fn):
            self.registered[name] = fn

    c = Client()
    runtime.register_governed_tools(c, {"tools": [{"name": "factory.run"}], "session": "s"})
    assert "factory.run" in c.registered
    out = c.registered["factory.run"](title="x", repo="/tmp")
    assert out.startswith("[denied]")  # capability gate is fail-closed


# --- API -------------------------------------------------------------------


def test_api_factory_run_merges(api_client, repo, good_patch):
    r = api_client.post(
        "/factory/run",
        json={"title": "Add a multiply function", "repo": str(repo), "builder": "patch", "patch_script": good_patch},
    )
    assert r.status_code == 200
    run = r.json()["run"]
    assert run["verdict"] == "MERGED"
    assert run["test"]["passed"] is True
    assert len(run["evidence_chain"]) == 6


def test_api_factory_run_requires_args(api_client):
    r = api_client.post("/factory/run", json={})
    assert r.status_code == 422
    r = api_client.post("/factory/run", json={"title": "x", "repo": "/no/such/dir"})
    assert r.status_code == 422


def test_api_factory_requires_operator(monkeypatch):
    monkeypatch.setattr(settings, "operator_token", "real-operator-token")
    r = TestClient(create_app()).post("/factory/run", json={"title": "x", "repo": "/tmp"})
    assert r.status_code == 401


# --- MCP bridge ------------------------------------------------------------


def test_mcp_manifest_has_factory(bridge_client):
    r = bridge_client.get("/mcp/tools", headers={"x-mcp-secret": SECRET})
    assert r.status_code == 200
    names = [t.get("name") for t in r.json().get("tools", [])]
    assert "factory_run" in names


def test_mcp_proxy_factory_run(bridge_client, repo, good_patch):
    r = _post(
        bridge_client,
        {"tool": "factory_run", "args": {"title": "Add a multiply function", "repo": str(repo), "builder": "patch", "patch_script": good_patch}},
    )
    assert r.status_code == 200
    run = r.json()["result"]
    assert run["verdict"] == "MERGED"
    assert run["verification"]["verdict"] == "PASS"


def test_mcp_proxy_factory_requires_args(bridge_client):
    r = _post(bridge_client, {"tool": "factory_run", "args": {}})
    assert r.status_code == 422
