from __future__ import annotations

import httpx

from msb_v3.infrastructure.qdrant_contract import preflight


def test_disabled_qdrant_is_explicit_environment_state(monkeypatch):
    monkeypatch.setenv("MSB_QDRANT_ENABLED", "0")
    result = preflight()
    assert result.classification == "ENVIRONMENT"
    assert not result.ready
    assert "disabled" in result.detail.lower()


def test_unreachable_qdrant_is_infrastructure(monkeypatch):
    monkeypatch.setenv("MSB_QDRANT_ENABLED", "1")
    monkeypatch.setenv("QDRANT_HOST", "127.0.0.1")
    monkeypatch.setenv("QDRANT_PORT", "1")
    result = preflight(timeout=0.05)
    assert result.classification == "INFRASTRUCTURE"
    assert not result.ready


def test_qdrant_contract_passes_with_expected_collection(monkeypatch):
    class Response:
        def __init__(self, ok=True):
            self.is_success = ok
        def raise_for_status(self):
            return None

    class Client:
        def __init__(self, **kwargs):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def get(self, path):
            return Response()

    monkeypatch.setattr(httpx, "Client", Client)
    result = preflight(expected_collection="tenant_test")
    assert result.ready
    assert result.classification == "PASS"
