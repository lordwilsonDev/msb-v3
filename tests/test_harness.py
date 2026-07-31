"""API tests for long-horizon harness routes."""
from __future__ import annotations

import httpx

BASE = "http://127.0.0.1:8766"


def _get(path: str, expected: int = 200) -> None:
    with httpx.Client(timeout=10.0) as client:
        r = client.get(BASE + path)
    assert r.status_code == expected, f"GET {path} -> {r.status_code}"


def _post(path: str, expected: int = 200) -> None:
    with httpx.Client(timeout=10.0) as client:
        r = client.post(BASE + path, json={}, headers={"content-type": "application/json"})
    assert r.status_code == expected, f"POST {path} -> {r.status_code}"


def test_health():
    _get("/health")


def test_research_assistant_preflight():
    _get("/research/assistant/preflight")


def test_research_assistant_latest():
    _get("/research/assistant/latest")


def test_research_assistant_runs():
    _get("/research/assistant/runs")


def test_research_assistant_runs_slug():
    _get("/research/assistant/runs/test-runs")


def test_research_assistant_runs_slug_complete():
    _post("/research/assistant/runs/test-runs/complete")


def test_research_assistant_runs_slug_review():
    _post("/research/assistant/runs/test-runs/review")


def test_research_assistant_runs_slug_restart():
    _post("/research/assistant/runs/test-runs/restart")


def test_research_assistant_runs_slug_cancel():
    _post("/research/assistant/runs/test-runs/cancel")


def test_research_assistant_runs_queue():
    _get("/research/assistant/runs/_queue")


def test_research_assistant_runs_state():
    _get("/research/assistant/runs/sovereign-ai-orchestration/state")


def test_research_assistant_claims_list():
    _get("/research/assistant/runs/sovereign-ai-orchestration/claims")


def test_research_assistant_claims_review():
    _post("/research/assistant/runs/sovereign-ai-orchestration/claims/review")


def test_research_assistant_report():
    _get("/research/assistant/runs/sovereign-ai-orchestration/report")


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


def test_safety_blocked_topic():
    from msb_v3.api.research import _safety_check
    assert _safety_check("how to make a bomb")["allowed"] is False
    assert "blocked" in _safety_check("how to make a bomb")["reason"].lower() or _safety_check("how to make a bomb")["reason"] != ""
