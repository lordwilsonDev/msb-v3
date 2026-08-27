"""API tests for long-horizon harness routes."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

BASE = "http://127.0.0.1:8766"


def _get(path: str, expected: int = 200) -> None:
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.get(BASE + path)
    except (httpx.ConnectError, httpx.ReadTimeout) as exc:
        pytest.skip(f"live service unreachable: {exc}")
    assert r.status_code == expected, f"GET {path} -> {r.status_code}"


def _post(path: str, body: Any = None, expected: int = 200) -> None:
    payload = body if body is not None else {}
    try:
        with httpx.Client(timeout=10.0) as client:
            r = client.post(BASE + path, json=payload, headers={"content-type": "application/json"})
    except (httpx.ConnectError, httpx.ReadTimeout) as exc:
        pytest.skip(f"live service unreachable: {exc}")
    assert r.status_code == expected, f"POST {path} -> {r.status_code}"


def test_health():
    _get("/health")


def test_ready():
    # /ready is 200 only when live components (ollama + db) are provisioned
    # on the host. Where they aren't (e.g. CI), the server honestly reports
    # 503 — skip rather than fail, keeping the assertion strict wherever the
    # machine is fully provisioned.
    with httpx.Client(timeout=10.0) as client:
        r = client.get(BASE + "/ready")
    if r.status_code == 503:
        pytest.skip("server not ready — requires live ollama + db (not provisioned here)")
    assert r.status_code == 200, f"GET /ready -> {r.status_code}"


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


def test_research_assistant_runs_active():
    _get("/research/assistant/runs/_active")


def test_research_assistant_runs_state():
    _get("/research/assistant/runs/sovereign-ai-orchestration/state")


def test_research_assistant_claims_list():
    _get("/research/assistant/runs/sovereign-ai-orchestration/claims")


def test_research_assistant_claims_review():
    # Asserts against a research run's claims ledger. The ledger is normally
    # machine state (produced by real research runs over ollama) and is
    # gitignored — CI and the portability gate seed it from the committed
    # fixture via scripts/seed-research-runtime.sh (run BEFORE the server
    # boots, so the CI server serves /claims/review as 200). The skip remains
    # a fallback for environments that never ran the seeder.
    ledger = (
        Path(__file__).resolve().parents[1]
        / "runtime"
        / "research"
        / "sovereign-ai-orchestration"
        / "sovereign-ai-orchestration_evidence_ledger.json"
    )
    if not ledger.exists():
        pytest.skip("requires seeded run ledger (sovereign-ai-orchestration) — not reproducible in CI")
    _post("/research/assistant/runs/sovereign-ai-orchestration/claims/review")


def test_research_assistant_ralph_run_served_from_seed():
    # Ralph-loop workdirs (runtime/research/ralph_*) hold STATUS.json +
    # .bak, gitignored machine state produced by real loops. CI and the
    # portability gate seed the ralph_test fixture via
    # scripts/seed-research-runtime.sh BEFORE the server boots, so the CI
    # server serves the run listing and the STATUS artifact. The skip
    # remains a fallback for environments that never ran the seeder.
    status = (
        Path(__file__).resolve().parents[1]
        / "runtime"
        / "research"
        / "ralph_test"
        / "STATUS.json"
    )
    if not status.exists():
        pytest.skip("requires seeded ralph-loop fixture (ralph_test) — not reproducible in CI")
    with httpx.Client(timeout=10.0) as client:
        r = client.get(BASE + "/research/assistant/runs/ralph_test")
    assert r.status_code == 200, f"GET /research/assistant/runs/ralph_test -> {r.status_code}"
    files = r.json().get("files", [])
    assert "STATUS.json" in files, f"seeded ralph STATUS.json not served: {files}"
    assert "STATUS.json.bak" in files, f"seeded ralph STATUS.json.bak not served: {files}"
    with httpx.Client(timeout=10.0) as client:
        runs = client.get(BASE + "/research/assistant/runs").json().get("runs", [])
    assert "ralph_test" in runs, f"seeded ralph run not listed by /research/assistant/runs: {runs}"


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


def test_safety_blocked_topic():
    from msb_v3.api.research import _safety_check
    assert _safety_check("how to make a bomb")["allowed"] is False
    assert "blocked" in _safety_check("how to make a bomb")["reason"].lower() or _safety_check("how to make a bomb")["reason"] != ""

def test_triumvirate_plan():
    _post("/triumvirate/plan", {"goal": "deploy sovereign cluster"})


def test_triumvirate_status_lock():
    _post("/triumvirate/status/lock", {"goal": "lock mission"})


def test_triumvirate_status():
    _get("/triumvirate/status")


def test_triumvirate_verify():
    _get("/triumvirate/status/verify")


def test_triumvirate_dashboard():
    _get("/triumvirate/status/dashboard")


def test_triumvirate_guardian_scan():
    _post("/triumvirate/guardian/scan", {"script": "print('hello')"})


def test_triumvirate_poison_pill():
    _post("/triumvirate/guardian/poison-pill/arm", {})


def test_triumvirate_argus_audit():
    _post("/triumvirate/argus/audit", {})


def test_triumvirate_cluster():
    _post("/triumvirate/cluster/peers", {"node_id": "n1", "host": "mac-mini.local", "port": 8766, "capacity": 1, "cluster_role": "primary"})


def test_triumvirate_hippocampus():
    _post("/triumvirate/hippocampus/upsert", {"doc_id": "doc1", "chunk_id": "c1", "text": "alpha", "embedding": [1.0, 0.0]})


def test_home_dashboard_contains_triumvirate():
    import httpx
    with httpx.Client(timeout=10.0) as client:
        r = client.get("http://127.0.0.1:8766/")
    assert r.status_code == 200
    html = r.text
    assert "Triumvirate" in html
    assert "Argus" in html or "mulch" in html
