"""Tests for the execute_task_contract CLI driving the demo dag.

The demo fixture (scripts/fixtures/demo_dag_3node.json) is a precondition
chain: a -> b -> c, with c declared to fail its predicates and roll back.
These tests pin that `--execute --write-back` advances exactly one node per
call and that three calls drive the chain to the intended ROLLED_BACK.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

CLI = ROOT / "scripts" / "execute_task_contract.py"
FIXTURE = ROOT / "scripts" / "fixtures" / "demo_dag_3node.json"


def _run_cli(dag: Path, output: Path, ledger: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CLI), "--execute", str(dag),
         "--output-root", str(output), "--ledger-dir", str(ledger),
         "--goal", "demo", *extra],
        capture_output=True, text=True, timeout=120,
    )


def _fresh_dag(tmp_path: Path) -> Path:
    dag = tmp_path / "dag.json"
    dag.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    return dag


def test_write_back_advances_exactly_one_node_and_persists(tmp_path):
    dag = _fresh_dag(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    r = _run_cli(dag, out, tmp_path / "ledger", "--write-back")
    assert r.returncode == 0, r.stdout + r.stderr
    statuses = {e["task_id"]: e["status"] for e in json.loads(dag.read_text(encoding="utf-8"))}
    assert statuses == {"a": "VERIFIED", "b": "READY", "c": "READY"}, statuses


def test_precondition_gating_without_write_back(tmp_path):
    """Without --write-back the file never advances — the CLI must still
    select the first READY node (a) each call (spec §9 one-node granularity)."""
    dag = _fresh_dag(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    ledger = tmp_path / "ledger"
    r1 = _run_cli(dag, out, ledger)
    r2 = _run_cli(dag, out, ledger)
    assert r1.returncode == 0 and r2.returncode == 0
    assert "executed a: VERIFIED" in r1.stdout
    assert "executed a: VERIFIED" in r2.stdout  # still a — file never advanced
    statuses = {e["task_id"]: e["status"] for e in json.loads(dag.read_text(encoding="utf-8"))}
    assert statuses == {"a": "READY", "b": "READY", "c": "READY"}


def test_full_drive_reaches_intended_rollback(tmp_path):
    dag = _fresh_dag(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    ledger = tmp_path / "ledger"
    seq = []
    for _ in range(3):
        r = _run_cli(dag, out, ledger, "--write-back")
        seq.append(r.returncode)
    statuses = {e["task_id"]: e["status"] for e in json.loads(dag.read_text(encoding="utf-8"))}
    assert statuses == {"a": "VERIFIED", "b": "VERIFIED", "c": "ROLLED_BACK"}, statuses
    assert seq == [0, 0, 1]  # c is the intended rollback -> CLI exit 1
    ev = (ledger / "records" / "task_events.jsonl").read_text(encoding="utf-8")
    assert "TASK_FAILED" in ev and '"task_id": "c"' in ev
    claims = json.loads((ledger / "claims.json").read_text(encoding="utf-8"))["claims"]
    assert len(claims) == 3  # claim:done:a, claim:done:b, claim:ok:c


def test_write_back_round_trips_all_contract_fields(tmp_path):
    """--write-back must round-trip entries losslessly: every valid contract
    field present on a node survives the [dict(e) for e in dag] shallow-copy
    path (unknown fields are already rejected by the validator)."""
    dag = _fresh_dag(tmp_path)
    entries = json.loads(dag.read_text(encoding="utf-8"))
    entries[0].update({
        "confidence": 0.92,
        "verification": "external",
        "allowed_data": ["tenant:alpha"],
        "constraints": {"budget_cap_usd": 0.5, "max_steps": 6},
    })
    dag.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    r = _run_cli(dag, out, tmp_path / "ledger", "--write-back")
    assert r.returncode == 0, r.stdout + r.stderr
    written = json.loads(dag.read_text(encoding="utf-8"))[0]
    assert written["status"] == "VERIFIED"
    assert written["confidence"] == 0.92
    assert written["verification"] == "external"
    assert written["allowed_data"] == ["tenant:alpha"]
    assert written["constraints"] == {"budget_cap_usd": 0.5, "max_steps": 6}


def test_write_back_refused_with_dry_run(tmp_path):
    dag = _fresh_dag(tmp_path)
    out = tmp_path / "out"
    out.mkdir()
    r = _run_cli(dag, out, tmp_path / "ledger", "--write-back", "--dry-run")
    assert r.returncode == 2
    assert "cannot combine" in r.stderr.lower()
    # nothing written
    statuses = {e["task_id"]: e["status"] for e in json.loads(dag.read_text(encoding="utf-8"))}
    assert statuses == {"a": "READY", "b": "READY", "c": "READY"}
