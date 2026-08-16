"""Smoke test for /models endpoint."""

from __future__ import annotations

from fastapi.testclient import TestClient

from msb_v3.api.app import create_app


def test_models_endpoint():
    client = TestClient(create_app())
    r = client.get("/models/", follow_redirects=False)
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert len(data) == 2
    backends = {m["backend"] for m in data}
    assert backends == {"ollama", "llamacpp"}


def test_models_switch():
    from msb_v3.core.config import settings
    from msb_v3.local_ai.client_factory import active_backend

    original = active_backend()
    client = TestClient(create_app())
    try:
        r = client.post("/models/switch", json={"backend": "llamacpp"})
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
        assert active_backend() == "llamacpp"
    finally:
        # /models/switch mutates the global active backend — restore it so
        # later tests (e.g. /system/health's active-backend semantics) see
        # the default and are order-independent.
        settings._active_backend = original
