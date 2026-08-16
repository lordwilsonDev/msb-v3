"""Pin every version-reporting surface to msb_v3.__version__.

Regression: /status, /health, and the FastAPI app reported a hardcoded
"0.1.0" while the package was at 0.2.3 (status.py, health.py, app.py,
studio.py — the last is the handler that actually serves /status). Any
endpoint that reports a version must agree with the package version, or
release tags and the running server drift apart.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from msb_v3 import __version__
from msb_v3.api.app import create_app


def _client() -> TestClient:
    return TestClient(create_app())


def test_status_reports_package_version() -> None:
    body = _client().get("/status").json()
    assert body["version"] == __version__


def test_status_absorbs_former_duplicate_router_fields() -> None:
    """M3 convergence: the never-mounted api/status.py router was deleted and
    its fields folded into the live studio /status route — pin them so a dead
    copy can't silently reappear."""
    body = _client().get("/status").json()
    assert body["ollama_url"]
    assert body["db_path"]


def test_health_reports_package_version() -> None:
    body = _client().get("/health").json()
    assert body["version"] == __version__


def test_system_info_reports_package_version() -> None:
    body = _client().get("/system/info").json()
    assert body["version"] == __version__


def test_system_config_reports_package_version() -> None:
    body = _client().get("/system/config").json()
    assert body["version"] == __version__


def test_app_version_matches_package_version() -> None:
    assert create_app().version == __version__
