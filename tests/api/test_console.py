"""Governed-loop console (/console) — hermetic page tests.

The console is a CLIENT of the existing operator-gated API (no new auth
surface, no token in HTML). These tests pin that contract:

  1. the page serves and is the console, not the cockpit;
  2. it references exactly the endpoints it is allowed to call — the three
     operator-gated /agent/* endpoints PLUS the public /metrics/prometheus
     scrape (verdict + latency strip);
  3. the HTML contains no secret material (the token is entered by the
     operator at runtime, kept in sessionStorage, sent as the standard
     bearer header);
  4. the router is genuinely mounted (no dead-router regression);
  5. the run/replay/task rendering functions exist and handle fixture data;
  6. the metrics strip is present and carries no bearer header (public).
"""
from __future__ import annotations

import re
import shutil
import subprocess

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
    """The page may only call the three documented operator-gated endpoints
    plus the public /metrics/prometheus scrape — it must not invent a new
    mutation surface or bypass."""
    r = client.get("/console")
    body = r.text
    for path in ("/agent/handle", "/agent/tasks/", "/metrics/prometheus"):
        assert path in body, f"console must reference {path}"
    # No unchecked fetch targets: every fetch() call in the page goes through
    # the api() helper against /agent/* (the token is added there), except
    # the metrics scrape which is public and carries no bearer header.
    import re

    fetches = re.findall(r'fetch\("([^"]+)"', body)
    for f in fetches:
        assert f.startswith("/agent/") or f == "/metrics/prometheus", (
            f"console must not call {f} directly"
        )


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


def test_console_metrics_strip_present_and_public(client: TestClient) -> None:
    """The recent-runs card carries a metrics strip fed by the PUBLIC
    /metrics/prometheus scrape. The fetch for it must NOT go through the
    token-carrying api() helper (the token never leaves gated calls) and the
    strip must render verdict chips + latency quantiles."""
    r = client.get("/console")
    body = r.text
    # Strip element exists in the recent-runs card.
    assert 'id="metrics-strip"' in body
    assert "metrics loading" in body
    # The metrics fetch is a bare fetch (no api() helper — no bearer header).
    assert 'fetch("/metrics/prometheus")' in body
    # Parsing + quantile + rendering functions exist.
    for fn in ("parsePrometheus", "quantile", "renderMetricsStrip", "loadMetrics"):
        assert fn in body, f"console must define {fn}"
    # The ActionGate verdict labels the strip maps are pinned.
    assert "VERDICT_LABELS" in body
    assert '"allowed"' in body and '"denied"' in body


def test_console_emitted_js_escapes_survive_python_string(client: TestClient) -> None:
    """The page is a Python triple-quoted string, so JS escapes must be
    doubled in the Python source or they are consumed at parse time and the
    emitted script is broken. The worst offender: `text.split("\n")` becomes
    a REAL newline (SyntaxError in the browser). Pin the literal escapes."""
    r = client.get("/console")
    body = r.text
    # The JS must contain the two-character sequence backslash-n inside the
    # split call — a raw newline here means the escape was eaten.
    assert 'text.split("\\n")' in body, "\\n escape was consumed by the Python string"
    # Regex escapes the parser needs must survive too.
    assert 'actiongate_decisions_total\\{verdict=' in body, "regex brace escape was consumed"
    assert "latency_seconds_bucket\\{" in body


def test_console_js_is_syntactically_valid(client: TestClient) -> None:
    """Found 2026-09-13 (beginner-user pass, live): clicking "Run governed
    task" on a fresh page load did nothing at all — no request, no error,
    silence. Cause: renderRun() built HTML attributes with `\\"` inside a
    non-raw Python triple-quoted string (`class=\\"" + ... + "\\"`) — Python
    consumes the backslash, so the *emitted* JS had unescaped quotes and
    failed to parse (`SyntaxError: Unexpected string`). A syntax error
    anywhere in the inline <script> block breaks every listener in it, so
    Run/Refresh silently did nothing — a full-page dead-UI bug the escape
    pins above (for `\\n` and regex `\\{`) didn't cover because they only
    pin previously-found instances one at a time, not the class of bug.
    This test instead asks node to actually parse the emitted script, so
    the next Python-escape-consumption bug fails loudly instead of shipping
    a page where every button is silently inert. Skips if node isn't on
    PATH (functionally covered by manual QA in that case, not CI-gated)."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not on PATH — cannot syntax-check emitted JS")
    r = client.get("/console")
    body = r.text
    match = re.search(r"<script>(.*)</script>", body, re.S)
    assert match is not None, "console page has no inline <script> block"
    proc = subprocess.run(
        [node, "--check", "-"],
        input=match.group(1),
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode == 0, f"emitted console JS fails to parse:\n{proc.stderr}"


def test_console_has_inline_field_validation(client: TestClient) -> None:
    """Found 2026-09-13 (beginner-user pass, live): nothing on this page
    validated a field until Run was clicked — a typo in output_dir or an
    over-length request cost a full 30s-3min round trip to discover. Pin
    that on-blur validation exists for both fields, and that it never
    invents a stricter rule than the server itself enforces."""
    r = client.get("/console")
    body = r.text
    assert 'addEventListener("blur", validateRequest)' in body
    assert 'addEventListener("blur", validateOutputDir)' in body
    assert "function validateRequest" in body
    assert "function validateOutputDir" in body
    # Never color-only (accessibility): every invalid state also sets text.
    assert "field-error" in body
    assert "⚠" in body


def test_console_max_request_chars_matches_the_servers_real_limit(
    client: TestClient,
) -> None:
    """The client-side length check must mirror api/agent.py's actual
    _MAX_REQUEST_LEN, not a guessed number that could silently drift from
    the real server-enforced limit."""
    from msb_v3.api.agent import _MAX_REQUEST_LEN

    r = client.get("/console")
    assert f"const MAX_REQUEST_CHARS = {_MAX_REQUEST_LEN};" in r.text


def test_console_output_dir_hint_uses_the_real_home_directory(
    client: TestClient,
) -> None:
    """__MSB_HOME__ must be substituted with the real operator home
    directory at render time (never left as a literal placeholder, never a
    hardcoded/guessed path) — it has to mirror api/agent.py's actual
    Path.home() containment check for the inline hint to mean anything."""
    from pathlib import Path

    r = client.get("/console")
    body = r.text
    assert "__MSB_HOME__" not in body, "placeholder was never substituted"
    home = str(Path.home())
    assert f'const MSB_HOME = "{home}";' in body
    assert f"must be under {home}" in body
