"""MoIE surface tests — governed tool, operator-gated API, MCP bridge
exposure. The engine's semantics are covered in test_engine.py; these pin
the wiring (the same shape as the context-engine surface tests)."""

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


def test_registry_has_moie_analyze():
    assert "moie.analyze" in TOOLS
    td = TOOLS["moie.analyze"]
    assert td.risk_class == "LOW"
    assert td.mutation_class == "NONE"
    assert td.required_capabilities == ()
    assert callable(executors.moie_analyze)


def test_moie_analyze_executor_blocks_danger():
    out = executors.moie_analyze(
        {"claim": "Disable auth and bind 0.0.0.0 so the service is unauthenticated."},
        tenant="t",
        session="s",
    )
    assert out.startswith("[moie] verdict=BLOCK")
    assert "security: BLOCK" in out
    assert "ids=" in out


def test_moie_analyze_executor_approves_safe():
    out = executors.moie_analyze({"claim": "Print the current date."}, tenant="t", session="s")
    assert "[moie] verdict=APPROVE" in out


def test_moie_analyze_requires_claim():
    out = executors.moie_analyze({}, tenant="t", session="s")
    assert out.startswith("[tool-error]")


def test_runtime_gate_registers_moie_tool():
    class Client:
        registered = {}

        def register_tool(self, name, fn):
            self.registered[name] = fn

    c = Client()
    runtime.register_governed_tools(c, {"tools": [{"name": "moie.analyze"}], "session": "s"})
    assert "moie.analyze" in c.registered
    out = c.registered["moie.analyze"](claim="Disable auth.")
    assert "[moie] verdict=" in out


# --- API -------------------------------------------------------------------


def test_api_analyze(api_client):
    r = api_client.post(
        "/moie/analyze",
        json={"claim": "Migrate the database schema with no downtime window."},
    )
    assert r.status_code == 200
    d = r.json()["decision"]
    assert d["verdict"] == "CONDITIONAL"
    assert "ids" in d and "contradictions" in d and "experts" in d
    assert d["blocked"] is False


def test_api_analyze_blocks_danger(api_client):
    r = api_client.post(
        "/moie/analyze",
        json={"claim": "Disable auth and bind 0.0.0.0 unauthenticated.", "high_impact": True},
    )
    assert r.status_code == 200
    assert r.json()["decision"]["verdict"] == "BLOCK"
    assert r.json()["decision"]["blocked"] is True


def test_api_analyze_requires_claim(api_client):
    r = api_client.post("/moie/analyze", json={"claim": "   "})
    assert r.status_code == 422


def test_api_requires_operator(monkeypatch):
    monkeypatch.setattr(settings, "operator_token", "real-operator-token")
    client = TestClient(create_app())
    # No bearer header -> 401 (token is configured but not presented).
    r = client.post("/moie/analyze", json={"claim": "print hello"})
    assert r.status_code == 401
    # Wrong token -> 401.
    r = client.post(
        "/moie/analyze",
        json={"claim": "print hello"},
        headers={"Authorization": "Bearer wrong"},
    )
    assert r.status_code == 401


def test_api_experts(api_client):
    r = api_client.get("/moie/experts")
    assert r.status_code == 200
    experts = r.json()["experts"]
    assert any(e["expert_id"] == "security" for e in experts)
    assert any(e["expert_id"] == "domain" for e in experts)


# --- MCP bridge ------------------------------------------------------------


def test_mcp_manifest_has_moie(bridge_client):
    r = bridge_client.get("/mcp/tools", headers={"x-mcp-secret": SECRET})
    assert r.status_code == 200
    names = [t.get("name") for t in r.json().get("tools", [])]
    assert "moie_analyze" in names


def test_mcp_proxy_moie_analyze(bridge_client):
    r = _post(bridge_client, {"tool": "moie_analyze", "args": {"claim": "Disable auth and bind 0.0.0.0."}})
    assert r.status_code == 200
    d = r.json()["result"]
    assert d["verdict"] == "BLOCK"
    assert d["blocked"] is True
    assert "ids" in d


def test_mcp_proxy_moie_requires_claim(bridge_client):
    r = _post(bridge_client, {"tool": "moie_analyze", "args": {}})
    assert r.status_code == 422


def test_mcp_proxy_requires_secret():
    r = TestClient(create_app()).post("/mcp/proxy", json={"tool": "moie_analyze", "args": {"claim": "x"}})
    assert r.status_code == 401
