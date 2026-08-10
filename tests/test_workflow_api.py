"""Tests for POST /workflow/advance — the Task Contract API.

The endpoint is the counterpart to /conversation/ask: it advances exactly one
READY dag node under its contract (docs/task-contract-v1.md §9), emits the §8
ledger evidence + TASK_FAILED event, and returns the advanced dag + ledger
verdicts. Driven with the demo 3-node fixture (a -> b -> c, c rolls back).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

FIXTURE = ROOT / "scripts" / "fixtures" / "demo_dag_3node.json"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MSB_CONVERSATION_MODEL", "stub")
    monkeypatch.setenv("MSB_CONVERSATION_LEDGER_DIR", str(tmp_path / "ledger"))
    monkeypatch.setenv("MSB_CONVERSATION_GIT_HEAD", "testhead")
    monkeypatch.setenv("MSB_WORKFLOW_OUTPUT_ROOT", str(tmp_path / "runs"))
    monkeypatch.delenv("MCP_BRIDGE_SECRET", raising=False)
    from msb_v3.api.app import create_app

    return TestClient(create_app())


def _demo_dag():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_advance_selects_first_ready_node(client):
    r = client.post("/workflow/advance", json={"dag": _demo_dag(), "goal": "demo"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["schema_version"] == "1.0"
    assert body["trace_id"]
    assert body["status"] == "advanced"
    statuses = {e["task_id"]: e["status"] for e in body["dag"]}
    assert statuses == {"a": "VERIFIED", "b": "READY", "c": "READY"}, statuses
    ex = body["executed"]
    assert ex["task_id"] == "a"
    assert ex["status"] == "VERIFIED" and ex["verdict"] == "VERIFIED"
    assert ex["claim_id"].startswith("claim:done:task:")
    assert ex["evidence_ref"].startswith("ledger://evidence/")
    assert ex["event"] is None


def test_drive_chain_to_intended_rollback(client, tmp_path):
    dag = _demo_dag()
    seq = []
    for _ in range(3):
        r = client.post("/workflow/advance", json={"dag": dag, "goal": "demo"})
        assert r.status_code == 200, r.text
        body = r.json()
        seq.append(body["executed"]["status"])
        dag = body["dag"]
    assert seq == ["VERIFIED", "VERIFIED", "ROLLED_BACK"], seq
    statuses = {e["task_id"]: e["status"] for e in dag}
    assert statuses == {"a": "VERIFIED", "b": "VERIFIED", "c": "ROLLED_BACK"}
    # ledger: 3 claims, c's availability claim carries negative evidence
    claims = json.loads((tmp_path / "ledger" / "claims.json").read_text(encoding="utf-8"))["claims"]
    assert len(claims) == 3
    assert any(c["claim_id"] == "claim:ok:task:c" and c["verdict"] == "UNVERIFIED" and c["negative_evidence"] for c in claims)
    ev = (tmp_path / "ledger" / "records" / "task_events.jsonl").read_text(encoding="utf-8")
    assert "TASK_FAILED" in ev and '"task_id": "c"' in ev
    assert body["executed"]["event"] == "TASK_FAILED"


def test_precondition_gating_selects_by_contract_not_order(client):
    """Reordering the dag must not change selection: b (precondition a) is
    never selected before a is VERIFIED."""
    dag = _demo_dag()
    dag = [dag[1], dag[2], dag[0]]  # b, c, a order
    r = client.post("/workflow/advance", json={"dag": dag})
    assert r.status_code == 200
    assert r.json()["executed"]["task_id"] == "a"


def test_noop_when_nothing_ready(client):
    """A valid dag with no READY nodes is a noop. (Note: REMOVING node a
    would be a 422 instead — b's inputs/preconditions reference a, and the
    contract rejects unknown task references.)"""
    dag = []
    for e in _demo_dag():
        e = dict(e)
        e["status"] = "VERIFIED"
        dag.append(e)
    r = client.post("/workflow/advance", json={"dag": dag})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "noop" and body["executed"] is None


def test_statusless_dag_is_safe_noop_not_silent_run(client):
    """Entries without an explicit READY status are never selected — the
    endpoint is a documented noop, never a silent execution."""
    dag = []
    for e in _demo_dag():
        e = dict(e)
        e.pop("status", None)
        dag.append(e)
    r = client.post("/workflow/advance", json={"dag": dag})
    assert r.status_code == 200
    assert r.json()["status"] == "noop"


def test_invalid_dag_is_422_envelope(client):
    r = client.post("/workflow/advance", json={"dag": [{"task_id": "x"}]})
    assert r.status_code == 422
    body = r.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "contract_invalid"
    assert body["trace_id"]


def test_empty_dag_is_422_envelope(client):
    r = client.post("/workflow/advance", json={"dag": []})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "contract_invalid"


def test_trace_id_echo(client):
    r = client.post("/workflow/advance", json={"dag": _demo_dag(), "trace_id": "tr_wf"})
    assert r.status_code == 200
    assert r.json()["trace_id"] == "tr_wf"


def test_dry_run_computes_without_writing(client, tmp_path):
    """dry_run passes through to the executor: verdicts are computed and
    returned, but no ledger claims/evidence are written."""
    r = client.post("/workflow/advance", json={"dag": _demo_dag(), "dry_run": True})
    assert r.status_code == 200, r.text
    assert r.json()["executed"]["status"] == "VERIFIED"
    assert not (tmp_path / "ledger" / "claims.json").exists()
    assert not (tmp_path / "ledger" / "evidence").exists()
    assert not (tmp_path / "ledger" / "records").exists()


def test_auth_gate(monkeypatch, tmp_path):
    monkeypatch.setenv("MSB_CONVERSATION_MODEL", "stub")
    monkeypatch.setenv("MSB_CONVERSATION_LEDGER_DIR", str(tmp_path / "ledger"))
    monkeypatch.setenv("MSB_CONVERSATION_GIT_HEAD", "testhead")
    monkeypatch.setenv("MSB_WORKFLOW_OUTPUT_ROOT", str(tmp_path / "runs"))
    monkeypatch.setenv("MCP_BRIDGE_SECRET", "supersecret")
    from msb_v3.api.app import create_app

    client = TestClient(create_app())
    r = client.post("/workflow/advance", json={"dag": _demo_dag()})
    assert r.status_code == 401
    r = client.post("/workflow/advance", json={"dag": _demo_dag()}, headers={"x-mcp-secret": "supersecret"})
    assert r.status_code == 200


def test_run_id_stable_tenant_scoped_workspace(client, tmp_path):
    """A run_id pins a stable TENANT-scoped scratch workspace (runs/<tenant>/<run_id>):
    node a's writes land there and survive for the next advance call, and one
    tenant can never see another tenant's leftover writes."""
    r = client.post("/workflow/advance", json={"dag": _demo_dag(), "run_id": "run-1"})
    assert r.status_code == 200
    assert (tmp_path / "runs" / "default" / "run-1" / "src" / "retrieval.py").exists(), \
        "run-scoped workspace must hold node a writes"
    r2 = client.post("/workflow/advance",
                     json={"dag": _demo_dag(), "run_id": "run-1", "tenant_id": "tenant:beta"})
    assert r2.status_code == 200
    assert (tmp_path / "runs" / "tenant_beta" / "run-1" / "src" / "retrieval.py").exists(), \
        "workspaces must be tenant-scoped"


def test_run_id_traversal_is_contained(tmp_path, monkeypatch):
    """Run ids can never escape the runs root — '..' falls back to a scratch
    root (contained, still serves the request)."""
    monkeypatch.setenv("MSB_WORKFLOW_OUTPUT_ROOT", str(tmp_path / "runs"))
    from msb_v3.api.workflow import _output_root_for

    base = (tmp_path / "runs").resolve()
    assert _output_root_for("default", "run-1") == base / "default" / "run-1"
    escaped = _output_root_for("default", "..")
    try:
        inside = os.path.commonpath([str(base), str(escaped)]) == str(base)
    except ValueError:
        inside = False
    assert not inside, "traversal run_id must fall back outside the runs root"
