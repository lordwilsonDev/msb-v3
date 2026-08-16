"""Gate for the tenant_chat placeholder (Phase 1 hardening 2026-08-15).

``tenant_chat.py`` is a documented placeholder — it echoes its input and was
never wired in because LLM routing is not yet tenant-scoped. This test pins
that it stays unmounted: it must not silently come back as a real route, and
the real /chat surface must keep serving.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from msb_v3.api.app import create_app


def test_tenant_chat_placeholder_is_not_mounted():
    from msb_v3.api import tenant_chat

    app = create_app()
    # The placeholder's own handler must not be reachable through any
    # mounted route (it defines POST /chat but must never be wired in — its
    # docstring says so, and this test enforces it). Path-based checks are
    # too weak here: the real /tenants/* router legitimately contains
    # "tenant", so we pin on the endpoint object itself.
    for r in app.routes:
        assert getattr(r, "endpoint", None) is not tenant_chat.chat
    # The real chat surface is mounted and reachable. FastAPI 0.141 keeps
    # included routers as lazy wrappers, so OpenAPI is the flattened route
    # registry (same source the /vesta/routes surface uses).
    assert "/chat" in app.openapi()["paths"]
    client = TestClient(app)
    assert client.get("/system/routes").status_code == 200
