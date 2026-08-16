"""Context Engine surface tests — governed tool, operator-gated API, MCP
bridge exposure. The engine's composition semantics are covered in
test_engine.py; these pin the wiring."""

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


def test_registry_has_context_compose():
    assert "context.compose" in TOOLS
    td = TOOLS["context.compose"]
    assert td.risk_class == "LOW"
    assert td.mutation_class == "NONE"
    assert td.required_capabilities == ()
    assert callable(executors.context_compose)


def test_context_compose_executor():
    out = executors.context_compose({"task": "check the engine"}, tenant="t", session="s")
    assert out.startswith("[context ")
    assert "tokens" in out
    assert "System: msb-v3" in out  # L0 present


def test_context_compose_requires_task():
    out = executors.context_compose({}, tenant="t", session="s")
    assert out.startswith("[tool-error]")


def test_runtime_gate_registers_context_tool():
    class Client:
        registered = {}

        def register_tool(self, name, fn):
            self.registered[name] = fn

    c = Client()
    runtime.register_governed_tools(c, {"tools": [{"name": "context.compose"}], "session": "s"})
    assert "context.compose" in c.registered
    out = c.registered["context.compose"](task="hello")
    assert "System: msb-v3" in out


# --- API -------------------------------------------------------------------


def test_api_compose(api_client):
    r = api_client.get("/context/compose", params={"task": "fix the auth bug"})
    assert r.status_code == 200
    pkg = r.json()["context"]
    assert "text" in pkg and "layers" in pkg
    assert pkg["total_tokens"] <= pkg["budget_tokens"]
    layer_ids = {layer["layer"] for layer in pkg["layers"]}
    assert {"L0", "L1"} <= layer_ids


def test_api_compose_requires_operator(monkeypatch):
    monkeypatch.setattr(settings, "operator_token", "test-operator-token")
    no_auth = TestClient(create_app())
    assert no_auth.get("/context/compose", params={"task": "x"}).status_code == 401


def test_api_compose_requires_task(api_client):
    assert api_client.get("/context/compose").status_code == 422


def test_api_compose_budget_clamped(api_client):
    r = api_client.get("/context/compose", params={"task": "t", "budget_tokens": 10})
    assert r.status_code == 200
    assert r.json()["context"]["budget_tokens"] >= 200  # clamped low bound


# --- MCP bridge -------------------------------------------------------------


def test_manifest_has_context_compose(bridge_client):
    names = {t["name"] for t in mcp_bridge._MCP_TOOLS}
    assert "context_compose" in names


def test_bridge_context_compose(bridge_client):
    r = _post(bridge_client, {"tool": "context_compose", "args": {"task": "understand the fabric"}})
    assert r.status_code == 200
    pkg = r.json()["result"]
    assert "System: msb-v3" in pkg["text"]
    assert pkg["reduction_pct"] >= 0


def test_bridge_requires_task(bridge_client):
    r = _post(bridge_client, {"tool": "context_compose", "args": {}})
    assert r.status_code == 422


def test_bridge_requires_secret(monkeypatch):
    no_secret = TestClient(create_app())
    assert no_secret.post("/mcp/proxy", json={"tool": "context_compose", "args": {"task": "x"}}).status_code == 401
