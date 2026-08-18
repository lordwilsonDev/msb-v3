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


def test_status_endpoint():
    from msb_v3.api.app import create_app

    app = create_app()
    client = TestClient(app)
    r = client.get("/status")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "msb-v3"
    assert "ready" in body
    assert body["model"] in {"deepseek-r1:1.5b", "qwen3:latest", "qwen3:8b"}


def test_system_routes():
    """M3 convergence: /system/routes derives from the live app's OpenAPI
    paths (the hand-maintained api/registry.py REGISTRY was deleted after
    it drifted — it listed 7 routers while app.py mounts 35+). Pin the shape
    and that the live surface is what's reported."""
    from msb_v3.api.app import create_app

    app = create_app()
    client = TestClient(app)
    r = client.get("/system/routes")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "msb-v3"
    assert "routes" in body
    paths = [route["path"] for route in body["routes"]]
    # The live surface must be reported — these are all genuinely mounted
    # routes, and the count reflects reality (way more than the 7 the old
    # registry hand-listed).
    assert "/chat" in paths
    assert "/system/routes" in paths
    assert "/agent/handle" in paths
    assert len(paths) > 20
    # Every reported route must carry its methods.
    for route in body["routes"]:
        assert isinstance(route["methods"], list) and route["methods"]
        assert isinstance(route["path"], str)


def test_system_config():
    from msb_v3 import __version__
    from msb_v3.api.app import create_app

    app = create_app()
    client = TestClient(app)
    r = client.get("/system/config")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "msb-v3"
    assert body["version"] == __version__
    assert "ready" in body
    assert "ollama_url" in body
    assert "ollama_model" in body


def test_memory_api(monkeypatch):
    from msb_v3.api.app import create_app

    # check_auth enforces when MCP_BRIDGE_SECRET is set (CI seeds it); the
    # native /memory surface is exercised here without auth, so stay in dev
    # mode regardless of the ambient env.
    monkeypatch.delenv("MCP_BRIDGE_SECRET", raising=False)

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
    # Phase 1: degradation is visible, never masked as success.
    assert result.ok is False
    assert result.event == "chat:degraded"
    assert result.error is not None and result.error.startswith("chat_degraded:")
    assert result.payload["text"].startswith("[fallback]")
    assert result.payload["model"] == "local-fallback"


def test_chat_includes_memory_history(monkeypatch):
    from msb_v3.api.app import create_app
    from msb_v3.harnesses.base import ChatHarness, HarnessResult
    from msb_v3.memory import store as memory_store
    from msb_v3.memory.store import Message

    # Same check_auth/dev-mode reasoning as test_memory_api above.
    monkeypatch.delenv("MCP_BRIDGE_SECRET", raising=False)

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
    from msb_v3.harnesses.base import ChatHarness

    class FakeClient:
        def generate(self, *args, **kwargs):
            raise ConnectionError("ollama unreachable")

    harness = ChatHarness(client=FakeClient())
    result = harness.execute("probe", session="metrics")
    assert result.ok is False  # Phase 1: fallback is degraded, not success
    assert result.telemetry.get("dispatcher") == "fallback"
    assert result.telemetry.get("model") == "local-fallback"
    # the failure class rides telemetry so the degraded state is diagnosable
    assert result.telemetry.get("failure", "")


def test_chat_harness_records_query_counter_and_latency_on_success():
    """Chaos-finding #5: Metrics.inc + Metrics.latency must move on the live
    chat path — queries_total and latency_seconds can no longer sit at zero."""
    from prometheus_client.registry import REGISTRY

    from msb_v3.harnesses.base import ChatHarness

    def sample(name: str, labels: dict) -> float:
        return REGISTRY.get_sample_value(name, labels) or 0.0

    class FakeClient:
        def execute_tool_loop(self, query, *, system=None, tools=None):
            class Resp:
                text = "ok"
                model = "fake"
                latency_s = 0.01

            return Resp()

    before_q = sample("msb_v3_queries_total", {"harness": "chat", "event": "chat:completed"})
    before_c = sample("msb_v3_latency_seconds_count", {"harness": "chat"})

    result = ChatHarness(client=FakeClient()).execute("probe", session="obs")

    assert result.ok is True
    after_q = sample("msb_v3_queries_total", {"harness": "chat", "event": "chat:completed"})
    after_c = sample("msb_v3_latency_seconds_count", {"harness": "chat"})
    after_sum = sample("msb_v3_latency_seconds_sum", {"harness": "chat"})
    assert after_q == before_q + 1
    assert after_c == before_c + 1
    assert after_sum > 0


def test_chat_harness_fallback_records_fallback_event_and_latency():
    """The fallback path counts chat:fallback (not silently dropped) and still
    records latency."""
    from prometheus_client.registry import REGISTRY

    from msb_v3.harnesses.base import ChatHarness

    def sample(name: str, labels: dict) -> float:
        return REGISTRY.get_sample_value(name, labels) or 0.0

    class FakeClient:
        def execute_tool_loop(self, query, *, system=None, tools=None):
            raise ConnectionError("ollama unreachable")

    before_q = sample("msb_v3_queries_total", {"harness": "chat", "event": "chat:fallback"})
    before_c = sample("msb_v3_latency_seconds_count", {"harness": "chat"})

    result = ChatHarness(client=FakeClient()).execute("probe", session="obs")

    assert result.ok is False  # Phase 1: degraded is surfaced, not ok=True
    assert result.payload["text"].startswith("[fallback]")
    after_q = sample("msb_v3_queries_total", {"harness": "chat", "event": "chat:fallback"})
    after_c = sample("msb_v3_latency_seconds_count", {"harness": "chat"})
    assert after_q == before_q + 1
    assert after_c == before_c + 1


def test_prometheus_scrape():
    from msb_v3.api.app import create_app

    app = create_app()
    client = TestClient(app)
    r = client.get("/metrics/prometheus")
    assert r.status_code == 200
    text = r.text
    assert "msb_v3_queries_total" in text
    assert "msb_v3_dispatcher_total" in text
    assert "msb_v3_latency_seconds" in text
    assert "msb_v3_ready" in text


def test_prometheus_scrape_is_real_text_format():
    """The scrape must be the Prometheus text format, not a JSON-escaped
    string. A bare `str` return in FastAPI is serialized as application/json
    with literal \\n escapes — that is NOT parseable as exposition text (a
    scraper, or the /console metrics strip, would fail). This pins the
    content-type and the actual line structure."""
    from msb_v3.api.app import create_app
    from msb_v3.observability.metrics import ACTIONGATE_DECISIONS, LATENCY

    client = TestClient(create_app())
    # The latency histogram and the actiongate counter are lazy — they only
    # emit sample lines after the first observe/increment. Touch both once so
    # the families exist in this isolated run.
    LATENCY.labels(harness="test").observe(0.25)
    ACTIONGATE_DECISIONS.labels(verdict="allowed").inc()
    r = client.get("/metrics/prometheus")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain"), (
        "Prometheus scrape must be text/plain, got " + r.headers["content-type"]
    )
    text = r.text
    # Real newlines, not literal \\n escapes inside one JSON string.
    assert "\\n" not in text, "scrape body is JSON-escaped, not plain text"
    # A sample line must parse as exposition text: name{labels} value.
    import re

    sample = next(
        (line for line in text.splitlines() if line.startswith("msb_v3_latency_seconds_bucket")),
        None,
    )
    assert sample is not None, "no latency bucket line in scrape"
    assert re.match(r"^msb_v3_latency_seconds_bucket\{[^}]*\} [0-9.e+]+$", sample), sample
    # And the ActionGate verdict family the console strip reads must be there.
    assert any(
        line.startswith('msb_v3_actiongate_decisions_total{verdict=') for line in text.splitlines()
    ), "actiongate verdict family missing from scrape"


def test_research_rate_limit_rejection_counts_on_prometheus():
    """The /research/assistant/run middleware refusal increments the
    msb_v3_rate_limit_rejections_total{limiter="run"} counter."""
    from starlette.requests import Request

    from msb_v3.api.app import _RUN_LIMITER, _RUN_RATE_LIMIT_MAX, create_app
    from msb_v3.observability.metrics import RATE_LIMIT_REJECTIONS

    # Pre-exhaust the run limiter with a synthetic request that keys exactly
    # like the middleware's (TestClient peer host is "testclient"), so the
    # single real POST drives the rejection path without 10 pipeline runs.
    client = TestClient(create_app())
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/research/assistant/run",
        "headers": [(b"host", b"testserver")],
        "client": ("testclient", 50000),
    }
    for _ in range(_RUN_RATE_LIMIT_MAX):
        assert _RUN_LIMITER.check(Request(scope))

    before = RATE_LIMIT_REJECTIONS.labels(limiter="run", reason="rate")._value.get()
    r = client.post("/research/assistant/run", json={"topic": "q"})
    assert r.status_code == 429
    assert RATE_LIMIT_REJECTIONS.labels(limiter="run", reason="rate")._value.get() == before + 1

    r = client.get("/metrics/prometheus")
    assert r.status_code == 200
    assert "msb_v3_rate_limit_rejections_total" in r.text


def test_execute_tool_loop_single_tool():
    from msb_v3.local_ai.ollama import LocalAIClient

    class FakeClient(LocalAIClient):
        calls = []

        def chat(self, messages, *, tools=None, temperature=0.2, max_tokens=2048):
            self.calls.append({"messages": messages, "tools": tools})

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
        def chat(self, messages, *, tools=None, temperature=0.2, max_tokens=2048):
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


def test_smi_query():
    from msb_v3.api.app import create_app

    app = create_app()
    client = TestClient(app)
    r = client.post("/smi/query", json={"query": "sovereign stack", "top_k": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "sovereign stack"
    assert len(body["matches"]) <= 2


def test_smi_evaluate():
    from msb_v3.api.app import create_app

    app = create_app()
    client = TestClient(app)
    r = client.post("/smi/evaluate", json={"subject": "model", "criteria": {"accuracy": 0.9}})
    assert r.status_code == 200
    body = r.json()
    assert "score" in body
    assert 0 <= body["score"] <= 1


def test_smi_adapt():
    from msb_v3.api.app import create_app

    app = create_app()
    client = TestClient(app)
    r = client.post("/smi/adapt", json={"source": "v1", "target": "v2"})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "v1"
    assert body["target"] == "v2"


def test_smi_report():
    from msb_v3.api.app import create_app

    app = create_app()
    client = TestClient(app)
    r = client.post("/smi/report", json={"slug": "demo", "format": "json"})
    assert r.status_code == 200
    body = r.json()
    assert body["slug"] == "demo"
    assert body["status"] == "generated"
