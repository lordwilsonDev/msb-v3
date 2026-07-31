"""API tests for long-horizon harness routes."""
from __future__ import annotations

import urllib.request
import urllib.error
import json


BASE = "http://127.0.0.1:8766"


def _get(path: str, expected: int = 200) -> None:
    with urllib.request.urlopen(BASE + path, timeout=3) as r:
        assert r.status == expected, f"GET {path} -> {r.status}"


def _post(path: str, expected: int = 200) -> None:
    payload = json.dumps({}).encode()
    req = urllib.request.Request(BASE + path, data=payload, headers={"content-type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            assert r.status == expected, f"POST {path} -> {r.status}"
    except urllib.error.HTTPError as e:
        assert e.code == expected, f"POST {path} -> {e.code}"


def test_health():
    _get("/health")


def test_research_assistant_preflight():
    _get("/research/assistant/preflight")


def test_research_assistant_state():
    _get("/research/assistant/state")


def test_research_assistant_latest():
    _get("/research/assistant/latest")


def test_research_assistant_runs():
    _get("/research/assistant/runs")


def test_research_assistant_runs_slug():
    _get("/research/assistant/runs/test-slug")


def test_research_assistant_runs_slug_complete():
    _post("/research/assistant/runs/test-slug/complete")


def test_research_assistant_runs_slug_review():
    _post("/research/assistant/runs/test-slug/review")


def test_research_assistant_memory_append():
    _post("/research/assistant/memory/append")


def test_safety_status():
    _get("/safety/status")


def test_safety_evaluate():
    _get("/safety/evaluate")


def test_safety_health():
    _get("/safety/health")


def test_safety_systems():
    _get("/safety/systems")


def test_evolution_scan():
    _get("/evolution/scan")


def test_evolution_memory_latest():
    _get("/evolution/memory/latest")


def test_evolution_memory_summary():
    _get("/evolution/memory/summary")


def test_evolution_memory_record():
    _post("/evolution/memory/record")


def test_evolution_memory_batch_update():
    _post("/evolution/memory/batch-update")


def test_evolution_continuity_resume_prompt():
    _post("/evolution/continuity/resume-prompt")


def test_mesh_discovery_peers():
    _post("/evolution/mesh/discovery/peers")


def test_memory_consolidate():
    _post("/evolution/memory/consolidate")


def test_sac_status():
    _get("/sac/status")


def test_echo_evaluate():
    _get("/echo/evaluate")


def test_schh_health():
    _get("/schh/health")


def test_systems_health_status():
    _get("/systems-health/status")


def test_sn_notify():
    _post("/sn/notify")
