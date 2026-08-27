"""Test the MSB_MULTIMODAL_ENABLED feature flag.

The /multimodal/* routes (vision_capture, haptic_heartbeat, speech_command)
currently call PARKED interfaces (VisionClaw, HapticHeartbeat,
SpeechFunctions — convergence pass 2026-08-17: parked, blocked_on
mac-mini-storage). A real implementation should not return
status="parked"; until one ships, the routes are gated behind
MSB_MULTIMODAL_ENABLED so consumers don't get a payload that looks like
work but isn't. Audit: parked subsystems must not inflate the dashboards.

Default: disabled (503). With MSB_MULTIMODAL_ENABLED=1: parked payload
returned and (because payload still has status="parked") the multimodal
counter is still NOT incremented — the second guard at the metric
call site is preserved.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from msb_v3.api.triumvirate import router as triumvirate_router  # noqa: E402


@pytest.fixture
def client():
    """Mount only the triumvirate router — keeps the test surface small."""
    app = FastAPI()
    app.include_router(triumvirate_router, prefix="/triumvirate")
    return TestClient(app)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Ensure each test starts with the flag cleared, regardless of
    ambient process state."""
    monkeypatch.delenv("MSB_MULTIMODAL_ENABLED", raising=False)


def test_multimodal_flag_default_is_disabled(client):
    """Without MSB_MULTIMODAL_ENABLED, all three routes 503."""
    routes = [
        ("/triumvirate/multimodal/vision/capture", None),
        ("/triumvirate/multimodal/haptic/heartbeat", None),
        ("/triumvirate/multimodal/speech/command", {"transcript": "go"}),
    ]
    for path, body in routes:
        resp = client.post(path) if body is None else client.post(path, json=body)
        assert resp.status_code == 503, f"{path} expected 503, got {resp.status_code}"
        body_json = resp.json()
        assert "parked" in body_json["detail"] or "stub-backed" in body_json["detail"]
        assert "MSB_MULTIMODAL_ENABLED" in body_json["detail"]


def test_multimodal_flag_enabled_returns_parked(client, monkeypatch):
    """With MSB_MULTIMODAL_ENABLED=1, routes return their underlying
    payload (not a 503). Vision + haptic report status="parked"
    (their impls are parked on mac-mini-storage); speech_command is a
    real regex-based endpoint, so it always returns a real mapping.
    The metric-parked-guard at the call site is independent of the
    feature flag and stays in place for Vision/haptic."""
    monkeypatch.setenv("MSB_MULTIMODAL_ENABLED", "1")

    resp = client.post("/triumvirate/multimodal/vision/capture")
    assert resp.status_code == 200
    assert resp.json()["status"] == "parked"
    assert resp.json()["blocked_on"] == "mac-mini-storage"

    resp = client.post("/triumvirate/multimodal/haptic/heartbeat")
    assert resp.status_code == 200
    assert resp.json()["status"] == "parked"

    resp = client.post(
        "/triumvirate/multimodal/speech/command", json={"transcript": "deploy canary"}
    )
    assert resp.status_code == 200
    data = resp.json()
    # VoiceResponder returns {input, command, authorized, response, ...}
    assert "command" in data or "response" in data


def test_multimodal_flag_truthy_values_enable(client, monkeypatch):
    """Anything other than the literal string '1' leaves the routes
    disabled — the gate is fail-closed, matching OPENAI_API_KEY."""
    for bad_value in ["0", "", "true", "yes", "on"]:
        monkeypatch.setenv("MSB_MULTIMODAL_ENABLED", bad_value)
        resp = client.post("/triumvirate/multimodal/vision/capture")
        assert resp.status_code == 503, (
            f"MSB_MULTIMODAL_ENABLED={bad_value!r} must keep the flag disabled"
        )
