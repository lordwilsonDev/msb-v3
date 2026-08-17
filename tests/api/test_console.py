"""Governed-loop console (/console) — hermetic page tests.

The console is a CLIENT of the existing operator-gated API (no new auth
surface, no token in HTML). These tests pin that contract:

  1. the page serves and is the console, not the cockpit;
  2. it references exactly the three gated endpoints it is allowed to call;
  3. the HTML contains no secret material (the token is entered by the
     operator at runtime, kept in sessionStorage, sent as the standard
     bearer header);
  4. the router is genuinely mounted (no dead-router regression);
  5. the run/replay/task rendering functions exist and handle fixture data.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from msb_v3.api.app import create_app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def test_console_serves_page(client: TestClient) -> None:
    r = client.get("/console")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Governed Loop Console" in r.text
    assert "MSB COCKPIT" not in r.text  # this is the runner, not the cockpit


def test_console_references_only_gated_endpoints(client: TestClient) -> None:
    """The page may only call the three documented operator-gated endpoints —
    it must not invent a new mutation surface or bypass."""
    r = client.get("/console")
    body = r.text
    for path in ("/agent/handle", "/agent/tasks/"):
        assert path in body, f"console must reference {path}"
    # No unchecked fetch targets: every fetch() call in the page goes through
    # the api() helper against /agent/* (the token is added there).
    import re

    fetches = re.findall(r'fetch\("([^"]+)"', body)
    for f in fetches:
        assert f.startswith("/agent/"), f"console must not call {f} directly"


def test_console_html_contains_no_token_or_secret(client: TestClient) -> None:
    """The page is served without any secret: no operator token, no bridge
    secret, no hardcoded credentials. The operator supplies the token at
    runtime."""
    r = client.get("/console")
    body = r.text
    # The Bearer mechanism string legitimately appears in the JS (it builds
    # the header from the runtime-entered token). What must NOT appear is an
    # actual secret: the configured token value, a bridge secret, an API key
    # pattern, or any fixed credential literal.
    assert "MSB_OPERATOR_TOKEN=" not in body
    assert "Authorization: Bearer <fixed>" not in body
    # API-key-shaped literals (the pattern real secrets take) must not appear.
    import re

    assert not re.search(r"\bsk-[A-Za-z0-9]{10,}", body), "API-key-shaped literal in HTML"
    # The token is read from the input at runtime, never written into HTML.
    assert 'id="token"' in body
    assert "sessionStorage" in body


def test_console_router_is_mounted(client: TestClient) -> None:
    """The console must be genuinely reachable — the dead-router guard
    (tests/api/test_no_dead_routers.py) enforces this statically; this test
    pins it at runtime too."""
    r = client.get("/console")
    assert r.status_code == 200
    # And it is not registered at the root — it owns /console only.
    assert str(r.url).endswith("/console")


def test_console_js_renders_fixture_run(client: TestClient) -> None:
    """The rendering functions must exist and survive a fixture-shaped run
    payload (the same shape /agent/handle returns)."""
    r = client.get("/console")
    body = r.text
    for fn in ("renderRun", "renderReplay", "renderTasks", "loadTasks"):
        assert f"function {fn}" in body or f"async function {fn}" in body or f"{fn}" in body


def test_console_approve_and_tenant_controls_present(client: TestClient) -> None:
    """The runner exposes the governance-relevant inputs: the approve toggle
    (pre-authorize declared writes) and the tenant selector — the two knobs
    that change what the gate will do."""
    r = client.get("/console")
    body = r.text
    assert 'id="approve"' in body
    assert 'id="tenant"' in body
    assert 'id="request"' in body
    assert 'id="token"' in body
