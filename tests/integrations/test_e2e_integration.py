"""End-to-end integration test — exercises the full request → response → evidence path.

This is the single most important verification gap (V1 in the Closer report).
It starts the FastAPI app via TestClient, hits critical endpoints, and verifies
that the system actually works end-to-end.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from msb_v3.api.app import create_app
from msb_v3.local_ai.ollama import LocalAIClient


def _app_client(monkeypatch) -> TestClient:
    """Create a TestClient with ollama stubbed out (may be down)."""
    monkeypatch.setattr(
        LocalAIClient,
        "generate",
        lambda self, *a, **k: (_ for _ in ()).throw(ConnectionError("ollama down")),
    )
    return TestClient(create_app())


# ── Health ───────────────────────────────────────────────────────────────


class TestHealthEndpoint:
    """The health endpoint must return 200 with correct structure."""

    def test_health_returns_200(self, monkeypatch):
        client = _app_client(monkeypatch)
        resp = client.get("/system/health")
        assert resp.status_code == 200

    def test_health_has_required_fields(self, monkeypatch):
        client = _app_client(monkeypatch)
        body = client.get("/system/health").json()
        assert "app" in body
        assert "db" in body
        assert body["app"] == "ok"
        assert body["db"] == "ok"

    def test_health_status_is_string(self, monkeypatch):
        client = _app_client(monkeypatch)
        body = client.get("/system/health").json()
        assert isinstance(body.get("status"), str)


# ── System Info ──────────────────────────────────────────────────────────


class TestSystemInfo:
    """System info endpoint must return project metadata."""

    def test_info_returns_200(self, monkeypatch):
        client = _app_client(monkeypatch)
        resp = client.get("/system/info")
        assert resp.status_code == 200

    def test_info_has_version(self, monkeypatch):
        client = _app_client(monkeypatch)
        body = client.get("/system/info").json()
        assert "version" in body or "name" in body


# ── PLEI ─────────────────────────────────────────────────────────────────


class TestPLEIEndpoints:
    """PLEI endpoints must return valid lifecycle analysis."""

    def test_plei_understand_returns_200(self, monkeypatch):
        client = _app_client(monkeypatch)
        resp = client.get("/plei/understand")
        assert resp.status_code == 200

    def test_plei_understand_has_lifecycle(self, monkeypatch):
        client = _app_client(monkeypatch)
        body = client.get("/plei/understand").json()
        assert "lifecycle" in body or "stage" in body

    def test_plei_gaps_returns_200(self, monkeypatch):
        client = _app_client(monkeypatch)
        resp = client.get("/plei/gaps")
        assert resp.status_code == 200

    def test_plei_risk_returns_200(self, monkeypatch):
        client = _app_client(monkeypatch)
        resp = client.get("/plei/risk")
        assert resp.status_code == 200

    def test_plei_risk_has_report(self, monkeypatch):
        client = _app_client(monkeypatch)
        body = client.get("/plei/risk").json()
        assert "risks" in body or "total_risks" in body

    def test_plei_dependencies_returns_200(self, monkeypatch):
        client = _app_client(monkeypatch)
        resp = client.get("/plei/dependencies")
        assert resp.status_code == 200


# ── Routes ───────────────────────────────────────────────────────────────


class TestRouteDiscovery:
    """All registered routes must be discoverable."""

    def test_routes_endpoint_returns_200(self, monkeypatch):
        client = _app_client(monkeypatch)
        resp = client.get("/system/routes")
        assert resp.status_code == 200

    def test_plei_routes_registered(self, monkeypatch):
        client = _app_client(monkeypatch)
        body = client.get("/system/routes").json()
        routes = body.get("routes", []) if isinstance(body, dict) else []
        plei_routes = [r for r in routes if "/plei" in str(r)]
        assert len(plei_routes) > 0, "No PLEI routes registered"


# ── Evidence Spine ───────────────────────────────────────────────────────


class TestEvidenceSpine:
    """Evidence spine must be accessible and chain-verifiable."""

    def test_spine_chain_valid(self):
        """Chain must be valid (even if empty in test env)."""
        from msb_v3.evidence.spine import DecisionEvidenceStore

        store = DecisionEvidenceStore()
        result = store.verify_chain()
        assert result["valid"] is True

    def test_spine_can_append_and_verify(self):
        """Spine must accept a record and verify the chain."""
        from msb_v3.evidence.spine import (
            DecisionEvidence,
            DecisionEvidenceStore,
        )

        store = DecisionEvidenceStore()
        ev = DecisionEvidence(
            task_id="e2e-test",
            policy_version="test",
            policy_result="PASS",
            risk_level="1",
            execution_id="e2e-test-exec",
            provider="test-provider",
        )
        record = store.append(ev)
        assert record.decision_id.startswith("decision_")
        assert len(record.content_hash) == 64  # SHA-256 hex

        # Chain must still be valid after append
        result = store.verify_chain()
        assert result["valid"] is True


# ── Calibration Store ────────────────────────────────────────────────────


class TestCalibrationStore:
    """Calibration store must be accessible and chain-verifiable."""

    def test_calibration_chain_intact(self, tmp_path):
        """Chain must be valid (even if empty in test env)."""
        from msb_v3.plei.calibration.store import CalibrationStore

        cal_path = tmp_path / "calibration.jsonl"
        cal = CalibrationStore(cal_path)
        ok, msg = cal.verify_chain()
        assert ok is True, f"Calibration chain broken: {msg}"

    def test_calibration_append_and_verify(self, tmp_path):
        """Store must accept records and maintain chain integrity."""
        from msb_v3.plei.calibration.store import (
            CalibrationStore,
            Outcome,
            Prediction,
        )

        cal_path = tmp_path / "calibration.jsonl"
        cal = CalibrationStore(cal_path)

        pred = Prediction(
            prediction_id="e2e-test-pred",
            project="test",
            forecast_at="2026-08-25T00:00:00Z",
            predicted_p50_days=10.0,
            predicted_p80_days=15.0,
            predicted_p95_days=20.0,
            predicted_mean_days=11.0,
            predicted_stdev_days=3.0,
            predicted_failure_probability=0.2,
            milestone_predictions={},
            confidence_level="low",
            coefficient_of_variation=0.27,
            trial_count=1000,
            seed=42,
            variables_used=3,
        )
        cal.record_prediction(pred)

        outcome = Outcome(
            outcome_id="e2e-test-outcome",
            prediction_id="e2e-test-pred",
            project="test",
            observed_at="2026-08-25T01:00:00Z",
            actual_duration_days=8.5,
            actual_completion=True,
            failures_encountered=0,
            severity="none",
            milestone_outcomes={},
            actual_stage="IMPLEMENTATION",
            step_count=1,
            error_note="",
        )
        cal.record_outcome(outcome)

        # Chain must be valid
        ok, msg = cal.verify_chain()
        assert ok is True, f"Chain broken after append: {msg}"

        # Must have 1 prediction, 1 outcome, 1 pair
        assert cal.prediction_count() == 1
        assert cal.outcome_count() == 1
        assert cal.pair_count() == 1


# ── Configuration ────────────────────────────────────────────────────────


class TestConfiguration:
    """Core configuration must be loadable and sane."""

    def test_settings_load(self):
        from msb_v3.core.config import settings

        assert settings.msb_home
        assert settings.llama_cpp_url
        assert "8081" in settings.llama_cpp_url  # updated default

    def test_settings_has_providers(self):
        from msb_v3.core.config import settings

        assert hasattr(settings, "deepseek_api_key")
        assert hasattr(settings, "deepseek_base_url")


# ── Provider Registry ────────────────────────────────────────────────────


class TestProviderRegistry:
    """Provider registry must have providers available."""

    def test_registry_has_providers(self):
        from msb_v3.agent.providers import ProviderRegistry

        reg = ProviderRegistry()
        assert len(reg._providers) > 0

    def test_registry_has_local_provider(self):
        from msb_v3.agent.providers import ProviderRegistry

        reg = ProviderRegistry()
        names = [p.__class__.__name__ for p in reg._providers]
        assert "LocalAgentProvider" in names
