"""Tests for the /cron control surface — operator-gated job management."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from msb_v3.api.app import create_app  # noqa: E402
from msb_v3.core.config import settings  # noqa: E402


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _open_operator(monkeypatch: pytest.MonkeyPatch, token: str = "tok") -> None:
    monkeypatch.setenv("MSB_OPERATOR_TOKEN", token)
    monkeypatch.setattr(settings, "operator_token", token)


def _create(client: TestClient, job_id: str = "j", **overrides) -> dict:
    body = {
        "job_id": job_id,
        "name": job_id.title(),
        "schedule": "0 2 * * *",
        "action": {"type": "health_check", "params": {}},
        "governance": {"max_retries": 1, "timeout_s": 30},
    }
    body.update(overrides)
    return body


def test_cron_requires_operator_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MSB_OPERATOR_TOKEN", raising=False)
    monkeypatch.setattr(settings, "operator_token", "")
    client = TestClient(create_app())
    r = client.get("/cron/jobs")
    assert r.status_code == 503  # token unset -> surface closed
    r = client.get("/cron/jobs", headers=_auth("wrong"))
    assert r.status_code == 503
    r = client.post("/cron/jobs", json={}, headers=_auth("wrong"))
    assert r.status_code == 503


def test_cron_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    _open_operator(monkeypatch)
    client = TestClient(create_app())
    # Missing fields.
    assert client.post("/cron/jobs", json={}, headers=_auth("tok")).status_code == 422
    # Bad schedule.
    r = client.post("/cron/jobs", json=_create(client, schedule="garbage"), headers=_auth("tok"))
    assert r.status_code == 422
    # Unknown action type.
    r = client.post("/cron/jobs", json=_create(client, action={"type": "nope"}), headers=_auth("tok"))
    assert r.status_code == 422


def test_cron_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    _open_operator(monkeypatch)
    client = TestClient(create_app())

    r = client.post("/cron/jobs", json=_create(client, job_id="daily-backup"), headers=_auth("tok"))
    assert r.status_code == 200
    job = r.json()["job"]
    assert job["schedule"] == "0 2 * * *"
    assert job["next_run"] is not None

    # Duplicate -> 409.
    r = client.post("/cron/jobs", json=_create(client, job_id="daily-backup"), headers=_auth("tok"))
    assert r.status_code == 409

    # List + get.
    r = client.get("/cron/jobs", headers=_auth("tok"))
    assert r.json()["count"] == 1
    r = client.get("/cron/jobs/daily-backup", headers=_auth("tok"))
    assert r.json()["job"]["action"]["type"] == "health_check"

    # Patch: disable + reschedule.
    r = client.patch(
        "/cron/jobs/daily-backup",
        json={"enabled": False, "schedule": "*/15 * * * *"},
        headers=_auth("tok"),
    )
    assert r.status_code == 200
    assert r.json()["job"]["enabled"] is False
    assert r.json()["job"]["schedule"] == "*/15 * * * *"

    # Unknown job -> 404.
    assert client.get("/cron/jobs/ghost", headers=_auth("tok")).status_code == 404
    assert client.delete("/cron/jobs/ghost", headers=_auth("tok")).status_code == 404

    # Delete.
    r = client.delete("/cron/jobs/daily-backup", headers=_auth("tok"))
    assert r.status_code == 200
    assert client.get("/cron/jobs", headers=_auth("tok")).json()["count"] == 0


def test_cron_run_and_history(monkeypatch: pytest.MonkeyPatch) -> None:
    _open_operator(monkeypatch)
    client = TestClient(create_app())
    client.post("/cron/jobs", json=_create(client, job_id="j"), headers=_auth("tok"))

    r = client.post("/cron/jobs/j/run", headers=_auth("tok"))
    assert r.status_code == 200
    assert r.json()["status"] == "SUCCESS"
    assert r.json()["attempts"] == 1

    r = client.get("/cron/jobs/j/history", headers=_auth("tok"))
    assert r.json()["count"] == 1
    assert r.json()["runs"][0]["status"] == "SUCCESS"

    # Unknown job run -> 404.
    r = client.post("/cron/jobs/ghost/run", headers=_auth("tok"))
    assert r.status_code == 404


def test_cron_status(monkeypatch: pytest.MonkeyPatch) -> None:
    _open_operator(monkeypatch)
    client = TestClient(create_app())
    r = client.get("/cron/status", headers=_auth("tok"))
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is False  # conftest disables the scheduler in tests
    assert body["job_count"] == 0


def test_requires_approval_job_runs_via_manual_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _open_operator(monkeypatch)
    client = TestClient(create_app())
    client.post(
        "/cron/jobs",
        json=_create(client, job_id="parked", governance={"requires_approval": True, "max_retries": 1, "timeout_s": 30}),
        headers=_auth("tok"),
    )
    # Manual run works — the operator token IS the approval.
    r = client.post("/cron/jobs/parked/run", headers=_auth("tok"))
    assert r.status_code == 200
    assert r.json()["status"] == "SUCCESS"
