"""Tests for sovereign core API."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))


def test_app_imports():
    spec = importlib.util.find_spec("msb_v3.api.app")
    assert spec is not None


def test_registry_items_are_unique():
    from msb_v3.api.registry import REGISTRY

    prefixes = [entry["prefix"] for entry in REGISTRY]
    assert len(prefixes) == len(set(prefixes))


def test_status_endpoint():
    from msb_v3.api.app import create_app

    app = create_app()
    client = TestClient(app)
    r = client.get("/status")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "msb-v3"
    assert "ready" in body
    assert body["model"] in {"deepseek-r1:1.5b", "qwen3:latest"}


def test_system_routes():
    from msb_v3.api.app import create_app

    app = create_app()
    client = TestClient(app)
    r = client.get("/system/routes")
    assert r.status_code == 200
    body = r.json()
    assert "routes" in body
    tags = [route["tags"] for route in body["routes"]]
    assert any("chat" in t for t in tags)


def test_system_config():
    from msb_v3.api.app import create_app

    app = create_app()
    client = TestClient(app)
    r = client.get("/system/config")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "msb-v3"
    assert body["version"] == "0.1.0"
    assert "ready" in body
    assert "ollama_url" in body
    assert "ollama_model" in body


def test_memory_api():
    from msb_v3.api.app import create_app

    app = create_app()
    client = TestClient(app)
    session = "test-session"

    r = client.get(f"/memory/{session}")
    assert r.status_code == 200
    assert r.json()["messages"] == []

    r = client.post(f"/memory/{session}", json={"role": "user", "content": "hello"})
    assert r.status_code == 200
    assert r.json()["messages"][0]["content"] == "hello"

    r = client.delete(f"/memory/{session}")
    assert r.status_code == 200
    assert r.json()["status"] == "cleared"


def test_chat_fallback_when_ollama_unreachable(monkeypatch):
    from msb_v3.harnesses.base import ChatHarness, HarnessResult

    class FakeClient:
        def generate(self, prompt, *, system=None, tools=None, temperature=0.2, max_tokens=2048):
            raise ConnectionError("ollama unreachable")

    harness = ChatHarness(client=FakeClient())
    result = harness.execute("hello", session="s1")
    assert isinstance(result, HarnessResult)
    assert result.ok is True
    assert result.event == "chat:completed"
    assert result.payload["text"].startswith("[fallback]")
    assert result.payload["model"] == "local-fallback"


def test_chat_includes_memory_history(monkeypatch):
    from msb_v3.api.app import create_app
    from msb_v3.harnesses.base import ChatHarness, HarnessResult
    from msb_v3.memory import store as memory_store
    from msb_v3.memory.store import Message

    def fake_recent(self, session, limit=50):
        return [Message("user", "hi"), Message("assistant", "hello")]

    monkeypatch.setattr(memory_store.MemoryStore, "recent", fake_recent)

    calls = {}

    class FakeHarness(ChatHarness):
        def execute(self, query, context=None, *, session="default", **kwargs):
            calls["session"] = session
            calls["context"] = context or {}
            return HarnessResult(ok=True, event="chat:completed", payload={"query": query, "text": "fake", "model": "fake"})

    app = create_app()
    app.state.chat = FakeHarness()

    client = TestClient(app)
    r = client.post("/chat", json={"query": "remember?", "session": "c1"})
    assert r.status_code == 200
    assert calls["session"] == "c1"
    assert "history" in calls["context"]
    assert "user: hi" in calls["context"]["history"]
    assert r.json()["history_count"] == 2


def test_dispatcher_metrics_increment():
    from msb_v3.harnesses.base import ChatHarness, HarnessResult

    class FakeClient:
        def generate(self, *args, **kwargs):
            raise ConnectionError("ollama unreachable")

    harness = ChatHarness(client=FakeClient())
    result = harness.execute("probe", session="metrics")
    assert result.ok is True
    assert result.telemetry.get("dispatcher") == "fallback"
    assert result.telemetry.get("model") == "local-fallback"


def test_prometheus_scrape():
    from msb_v3.api.app import create_app

    app = create_app()
    client = TestClient(app)
    r = client.get("/metrics/prometheus")
    assert r.status_code == 200
    text = r.text
    assert "msb_v3_queries_total" in text
    assert "msb_v3_dispatcher_total" in text
    assert "msb_v3_ready" in text


def test_execute_tool_loop_single_tool():
    from msb_v3.local_ai.ollama import LocalAIClient

    class FakeClient(LocalAIClient):
        calls = []

        def generate(self, prompt, *, system=None, tools=None, temperature=0.2, max_tokens=2048):
            self.calls.append({"prompt": prompt, "tools": tools})

            class Resp:
                text = "[tool-call]"
                model = "fake"
                latency_s = 0.0
                tool_calls = [{"function": {"name": "echo", "arguments": {"value": "ping"}}}]

            return Resp()

    client = FakeClient()
    client.register_tool("echo", lambda value: f"echo:{value}")
    resp = client.execute_tool_loop(
        "run echo",
        tools=[{"type": "function", "name": "echo", "parameters": {}}],
        max_tokens=2048,
        max_steps=1,
    )
    assert resp.text == "[tool-call]"
    assert len(client.calls) >= 1
    assert client.calls[0]["tools"][0]["name"] == "echo"


def test_execute_tool_loop_runs_tool():
    from msb_v3.local_ai.ollama import LocalAIClient

    class ToolLoopClient(LocalAIClient):
        def generate(self, prompt, *, system=None, tools=None, temperature=0.2, max_tokens=2048):
            class Resp:
                text = "done"
                model = "fake"
                latency_s = 0.0
                tool_calls = [{"function": {"name": "echo", "arguments": {"value": "x"}}}]
            return Resp()

    client = ToolLoopClient()
    client.register_tool("echo", lambda value: f"echo:{value}")
    resp = client.execute_tool_loop("run", tools=[{"type": "function", "name": "echo", "parameters": {}}], max_steps=1)
    assert resp.text == "done"
