"""Observability — Prometheus metrics + health endpoint.

Proves the observability subsystem actually works:
1. Metrics accumulate when the Metrics class is called
2. Health endpoint returns real subsystem status (not just "ok")
3. Prometheus format is valid and contains registered families
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from prometheus_client import generate_latest

from msb_v3.api.app import create_app
from msb_v3.local_ai.ollama import LocalAIClient
from msb_v3.observability.metrics import (
    ACTIONGATE_DECISIONS,
    DISPATCHER_EVENTS,
    LATENCY,
    MODEL_CALLS,
    QUERIES,
    READY,
    TASK_RECOVERIES,
    TASK_RETRIES,
    Metrics,
)

# --- Metrics accumulation ---


class TestMetricsAccumulate:
    """Prove: when Metrics methods are called, Prometheus counters move."""

    def test_queries_counter_increments(self):
        before = QUERIES.labels(harness="test", event="start")._value.get()
        Metrics.inc("test", "start")
        after = QUERIES.labels(harness="test", event="start")._value.get()
        assert after == before + 1

    def test_latency_histogram_observes(self):
        before_count = LATENCY.labels(harness="test")._sum.get()
        Metrics.latency("test", 0.5)
        after_count = LATENCY.labels(harness="test")._sum.get()
        assert after_count > before_count

    def test_retry_counter_increments(self):
        before = TASK_RETRIES.labels(harness="test")._value.get()
        Metrics.retry("test")
        after = TASK_RETRIES.labels(harness="test")._value.get()
        assert after == before + 1

    def test_recovery_counter_increments(self):
        before = TASK_RECOVERIES.labels(harness="test")._value.get()
        Metrics.recovered("test")
        after = TASK_RECOVERIES.labels(harness="test")._value.get()
        assert after == before + 1

    def test_ready_gauge_toggles(self):
        Metrics.set_ready(True)
        assert READY._value.get() == 1
        Metrics.set_ready(False)
        assert READY._value.get() == 0
        Metrics.set_ready(True)  # restore

    def test_dispatcher_counter_increments(self):
        before = DISPATCHER_EVENTS.labels(dispatcher="test_disp")._value.get()
        Metrics.inc_dispatcher("test_disp")
        after = DISPATCHER_EVENTS.labels(dispatcher="test_disp")._value.get()
        assert after == before + 1

    def test_actiongate_counter_increments(self):
        before = ACTIONGATE_DECISIONS.labels(verdict="SAFE")._value.get()
        ACTIONGATE_DECISIONS.labels(verdict="SAFE").inc()
        after = ACTIONGATE_DECISIONS.labels(verdict="SAFE")._value.get()
        assert after == before + 1

    def test_model_calls_counter_increments(self):
        before = MODEL_CALLS.labels(harness="test")._value.get()
        MODEL_CALLS.labels(harness="test").inc()
        after = MODEL_CALLS.labels(harness="test")._value.get()
        assert after == before + 1


# --- Prometheus output ---


class TestPrometheusFormat:
    """Prove: the Prometheus text output is valid and contains expected families."""

    def test_prometheus_output_contains_families(self):
        output = generate_latest().decode("utf-8")
        # Core families must be present
        assert "msb_v3_queries_total" in output
        assert "msb_v3_latency_seconds" in output
        assert "msb_v3_ready" in output
        assert "msb_v3_actiongate_decisions_total" in output
        assert "msb_v3_model_calls_total" in output

    def test_prometheus_output_is_valid_text(self):
        output = generate_latest().decode("utf-8")
        # Every line is either a comment (#), a metric family declaration,
        # or a metric sample (name + value)
        for line in output.strip().split("\n"):
            if not line.strip():
                continue
            assert line.startswith("#") or " " in line or "{" in line, f"Invalid line: {line!r}"


# --- Health endpoint ---


class TestHealthEndpoint:
    """Prove: /system/health returns real subsystem status."""

    def test_health_returns_200_with_components(self, monkeypatch):
        monkeypatch.setattr(
            LocalAIClient, "generate",
            lambda self, *a, **k: (_ for _ in ()).throw(ConnectionError("test")),
        )
        client = TestClient(create_app())
        resp = client.get("/system/health")
        assert resp.status_code == 200
        body = resp.json()
        assert "app" in body
        assert "db" in body
        assert "status" in body
        assert body["status"] in ("healthy", "degraded")

    def test_health_has_component_view(self, monkeypatch):
        monkeypatch.setattr(
            LocalAIClient, "generate",
            lambda self, *a, **k: (_ for _ in ()).throw(ConnectionError("test")),
        )
        client = TestClient(create_app())
        body = client.get("/system/health").json()
        assert "components" in body
        components = body["components"]
        # Core components must be present
        assert "api" in components
        assert "db" in components
        # Each component has status + detail
        for name, comp in components.items():
            assert "status" in comp, f"Component {name} missing status"
            assert comp["status"] in ("HEALTHY", "DEGRADED", "FAILED", "UNKNOWN")

    def test_health_overall_reflects_components(self, monkeypatch):
        monkeypatch.setattr(
            LocalAIClient, "generate",
            lambda self, *a, **k: "ok",
        )
        from msb_v3.core.config import settings
        monkeypatch.setattr(settings, "_active_backend", "ollama")
        client = TestClient(create_app())
        body = client.get("/system/health").json()
        # If no component is FAILED, overall is not FAILED
        any_failed = any(
            c.get("status") == "FAILED"
            for c in body.get("components", {}).values()
        )
        if any_failed:
            assert body["overall"] == "FAILED"
        else:
            assert body["overall"] in ("healthy", "degraded")

    def test_metrics_endpoint_returns_json(self):
        client = TestClient(create_app())
        resp = client.get("/metrics/")
        assert resp.status_code == 200
        body = resp.json()
        assert "ready" in body
        assert "prometheus" in body
        assert body["prometheus"] == "/metrics/prometheus"

    def test_metrics_prometheus_returns_text(self):
        client = TestClient(create_app())
        resp = client.get("/metrics/prometheus")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers["content-type"]
        assert "msb_v3_" in resp.text
