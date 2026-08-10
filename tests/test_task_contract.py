"""Tests for the Task Contract validator (docs/task-contract-v1.md).

Covers the task_id discriminator, contract field rules, the caps that stop
graph explosion (depth/nodes/per-level), the predicate registry + runner
(deterministic, zero-spend, no eval), claim derivation pinned to the replay
consumer, and the endpoint's 422 contract_invalid wiring.
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from msb_v3.conversation import task_contract as tc  # noqa: E402


def _contract(**overrides) -> dict:
    entry = {
        "task_id": "memory.003",
        "objective": "Implement semantic retrieval",
        "skill": "retrieval",
        "args": {"collection": "vault"},
        "inputs": ["memory.002:out"],
        "allowed_tools": ["local_llm", "filesystem"],
        "allowed_data": ["tenant:default"],
        "constraints": {"budget_cap_usd": 0.01, "max_steps": 8, "stall_threshold": 3},
        "preconditions": ["memory.002:VERIFIED"],
        "expected_output": {
            "schema": {"type": "object"},
            "predicates": [
                {"kind": "file_exists", "path": "out/retrieval.py"},
                {"kind": "file_contains", "path": "out/retrieval.py", "text": "def search"},
            ],
        },
        "verification": "on_submit",
        "side_effects": [{"kind": "file_write", "path": "out/**"}],
        "rollback": {"kind": "git_revert", "scope": "out/"},
        "confidence": 0.91,
        "parent": None,
        "status": "READY",
    }
    entry.update(overrides)
    return entry


def _dag(*entries) -> list[dict]:
    return list(entries)


# --------------------------------------------------------------------------
# the discriminator: task_id present ⇒ contract; absent ⇒ legacy unverified
# --------------------------------------------------------------------------


def test_legacy_dag_entry_allowed():
    assert tc.validate_contract({"skill": "retrieval", "args": {}}, set()) == []


def test_legacy_entry_rejects_unknown_fields():
    errs = tc.validate_contract({"skill": "retrieval", "objective": "sneaky"}, set())
    assert any("unknown fields" in e for e in errs)
    errs = tc.validate_contract({"args": {}}, set())  # missing skill
    assert any("skill" in e for e in errs)


def test_predicate_runner_never_crashes_on_malformed_predicate(tmp_path):
    # A malformed predicate (missing required arg, reachable when run_predicates
    # is called directly with unvalidated input) is a FAILED outcome, never a
    # crash — per the spec's no-crash contract.
    outcomes = tc.run_predicates([{"kind": "file_exists"}], tmp_path, {"exit_code": 0})
    assert len(outcomes) == 1
    assert outcomes[0]["passed"] is False
    assert "path" in outcomes[0]["detail"]


def test_full_contract_valid():
    errs = tc.validate_contract(_contract(), {"memory.002"})
    assert errs == []


# --------------------------------------------------------------------------
# contract field rules (§2)
# --------------------------------------------------------------------------


def test_required_fields():
    errs = tc.validate_contract(_contract(objective=""), {"memory.002"})
    assert any("objective" in e for e in errs)
    errs = tc.validate_contract(_contract(skill=""), {"memory.002"})
    assert any("skill" in e for e in errs)
    errs = tc.validate_contract(_contract(task_id=""), {"memory.002"})
    assert any("task_id" in e for e in errs)


def test_unknown_contract_field_rejected():
    errs = tc.validate_contract(_contract(bogus_field=1), {"memory.002"})
    assert any("bogus_field" in e for e in errs)


def test_invalid_status_rejected():
    errs = tc.validate_contract(_contract(status="HOPEFUL"), {"memory.002"})
    assert any("status" in e for e in errs)


def test_input_forward_and_self_refs_rejected():
    errs = tc.validate_contract(_contract(inputs=["memory.002:out"]), {"memory.002"})
    assert errs == []  # earlier task is fine
    errs = tc.validate_contract(_contract(inputs=["memory.004"]), {"memory.002"})
    assert any("unknown task" in e for e in errs)
    errs = tc.validate_contract(_contract(inputs=["memory.003"]), {"memory.002"})
    assert any("itself" in e for e in errs)


def test_permission_envelope_types():
    errs = tc.validate_contract(_contract(allowed_tools="local_llm"), {"memory.002"})
    assert any("allowed_tools" in e for e in errs)
    errs = tc.validate_contract(_contract(allowed_data=[1, 2]), {"memory.002"})
    assert any("allowed_data" in e for e in errs)


def test_constraints_rules():
    errs = tc.validate_contract(_contract(constraints={"budget_cap_usd": -1}), {"memory.002"})
    assert any("negative" in e for e in errs)
    errs = tc.validate_contract(_contract(constraints={"max_steps": 0}), {"memory.002"})
    assert any("max_steps" in e for e in errs)
    errs = tc.validate_contract(_contract(constraints={"mystery": 1}), {"memory.002"})
    assert any("mystery" in e for e in errs)


def test_preconditions_rules():
    errs = tc.validate_contract(_contract(preconditions=["memory.002"]), {"memory.002"})
    assert any("task_id:VERIFIED" in e for e in errs)
    errs = tc.validate_contract(_contract(preconditions=["ghost:VERIFIED"]), {"memory.002"})
    assert any("unknown task" in e for e in errs)
    errs = tc.validate_contract(_contract(preconditions=["memory.002:COMPLETED"]), {"memory.002"})
    assert any("VERIFIED" in e for e in errs)


def test_confidence_and_rollback_rules():
    errs = tc.validate_contract(_contract(confidence=1.5), {"memory.002"})
    assert any("confidence" in e for e in errs)
    errs = tc.validate_contract(_contract(rollback={"kind": "state_restore", "scope": "x"}), {"memory.002"})
    assert any("rollback kind" in e for e in errs)
    errs = tc.validate_contract(_contract(rollback={"kind": "git_revert"}), {"memory.002"})
    assert any("scope" in e for e in errs)


def test_parent_must_be_known():
    errs = tc.validate_contract(_contract(parent="ghost"), {"memory.002"})
    assert any("parent" in e for e in errs)
    assert tc.validate_contract(_contract(parent="memory.002"), {"memory.002"}) == []


# --------------------------------------------------------------------------
# caps (§7 — the graph-explosion governor, enforced at validation)
# --------------------------------------------------------------------------


def test_duplicate_task_id_rejected():
    errs = tc.validate_dag([_contract(), _contract(task_id="memory.003")])
    assert any("duplicate" in e for e in errs)


def test_depth_cap():
    # chain a → b → c → d = depth 4 > 3
    dag = [
        _contract(task_id="t1", parent=None),
        _contract(task_id="t2", parent="t1"),
        _contract(task_id="t3", parent="t2"),
        _contract(task_id="t4", parent="t3"),
    ]
    errs = tc.validate_dag(dag)
    assert any("max_dag_depth" in e for e in errs)
    # depth 3 is fine
    shallow = dag[:-1]
    assert not any("max_dag_depth" in e for e in tc.validate_dag(shallow))


def test_parent_cycle_detected():
    dag = [
        _contract(task_id="t1", parent="t2"),
        _contract(task_id="t2", parent="t1"),
    ]
    errs = tc.validate_dag(dag)
    assert any("max_dag_depth" in e for e in errs)


def test_node_count_cap():
    dag = [_contract(task_id=f"t{i}") for i in range(tc.MAX_DAG_NODES + 1)]
    errs = tc.validate_dag(dag)
    assert any("max_dag_nodes" in e for e in errs)


def test_per_level_cap():
    dag = [_contract(task_id=f"t{i}", parent="root") for i in range(tc.MAX_NODES_PER_LEVEL + 1)]
    errs = tc.validate_dag(dag)
    assert any("max_nodes_per_level" in e for e in errs)


def test_validate_workflow_goal_and_step_tracker():
    assert tc.validate_workflow({"goal": "g", "dag": []}) == []
    errs = tc.validate_workflow({"goal": "", "dag": []})
    assert any("goal" in e for e in errs)
    errs = tc.validate_workflow({"goal": "g", "dag": [], "step_tracker": {"required_steps": "nope"}})
    assert any("required_steps" in e for e in errs)


# --------------------------------------------------------------------------
# predicates (§4 — registry is the ONLY verification surface)
# --------------------------------------------------------------------------


def test_exit_code_alone_rejected():
    errs = tc.validate_contract(
        _contract(expected_output={"predicates": [{"kind": "exit_code", "code": 0}]}),
        {"memory.002"},
    )
    assert any("stronger than exit_code" in e for e in errs)


def test_unknown_predicate_kind_rejected():
    errs = tc.validate_contract(
        _contract(expected_output={"predicates": [{"kind": "custom", "fn": "eval(...)"}]}),
        {"memory.002"},
    )
    assert any("unknown kind" in e for e in errs)


def test_predicate_required_args():
    errs = tc.validate_contract(
        _contract(expected_output={"predicates": [{"kind": "file_contains", "path": "x"}]}),
        {"memory.002"},
    )
    assert any("text" in e for e in errs)
    errs = tc.validate_contract(
        _contract(expected_output={"predicates": [{"kind": "file_exists"}]}),
        {"memory.002"},
    )
    assert any("path" in e for e in errs)


def test_predicate_unknown_args_rejected():
    errs = tc.validate_contract(
        _contract(expected_output={"predicates": [{"kind": "file_exists", "path": "x", "evil": 1}]}),
        {"memory.002"},
    )
    assert any("evil" in e for e in errs)


# --------------------------------------------------------------------------
# claim derivation (pinned to the replay consumer)
# --------------------------------------------------------------------------


def test_claim_ok_task_matches_replay_consumer():
    # replay consumer: subject = f"task:{task_id}", claim_id = f"claim:ok:{subject}"
    task_id = "task_abc"
    assert tc.claim_ok_task(task_id) == f"claim:ok:task:{task_id}"


def test_claim_done_task_deterministic_and_content_sensitive():
    a = tc.claim_done_task("t1", {"schema": {}}, [{"kind": "file_exists", "path": "x"}])
    b = tc.claim_done_task("t1", {"schema": {}}, [{"kind": "file_exists", "path": "x"}])
    assert a == b
    assert tc.claim_done_task("t2", {"schema": {}}, [{"kind": "file_exists", "path": "x"}]) != a
    assert tc.claim_done_task("t1", {"schema": {"type": "object"}}, [{"kind": "file_exists", "path": "x"}]) != a
    assert a.startswith("claim:done:task:")


# --------------------------------------------------------------------------
# predicate runner (deterministic, zero-spend)
# --------------------------------------------------------------------------


def test_predicate_runner_all_kinds(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "retrieval.py").write_text("def search():\n    pass\n")
    preds = [
        {"kind": "exit_code", "code": 0},
        {"kind": "file_exists", "path": "out/retrieval.py"},
        {"kind": "file_contains", "path": "out/retrieval.py", "text": "def search"},
        {"kind": "file_not_contains", "path": "out/retrieval.py", "text": "forbidden"},
        {"kind": "output_equals", "expected": {"module": "retrieval"}},
        {"kind": "artifact_hash", "path": "out/retrieval.py", "sha256": hashlib.sha256((tmp_path / "out" / "retrieval.py").read_bytes()).hexdigest()},
    ]
    outcomes = tc.run_predicates(preds, tmp_path, {"exit_code": 0, "output": {"module": "retrieval"}})
    assert tc.predicates_pass(outcomes)
    assert len(outcomes) == 6
    for o in outcomes:
        assert o["passed"] is True


def test_predicate_failures_recorded_not_crash(tmp_path):
    (tmp_path / "out").mkdir()
    preds = [
        {"kind": "exit_code", "code": 1},
        {"kind": "file_exists", "path": "out/missing.py"},
        {"kind": "file_contains", "path": "out/a.py", "text": "x"},
        {"kind": "output_equals", "expected": {"a": 1}},
        {"kind": "artifact_hash", "path": "out/missing.py", "sha256": "abc"},
        {"kind": "not-a-kind"},
    ]
    outcomes = tc.run_predicates(preds, tmp_path, {"exit_code": 0, "output": {"a": 2}})
    assert not tc.predicates_pass(outcomes)
    assert [o["kind"] for o in outcomes] == [p["kind"] for p in preds]
    assert all(o["passed"] is False for o in outcomes)


def test_predicate_path_traversal_fails(tmp_path):
    outside = tmp_path / "secret.txt"
    outside.write_text("classified")
    preds = [{"kind": "file_contains", "path": "../secret.txt", "text": "classified"}]
    outcomes = tc.run_predicates(preds, tmp_path / "out", {"exit_code": 0})
    assert outcomes[0]["passed"] is False
    assert "escapes output scope" in outcomes[0]["detail"]


# --------------------------------------------------------------------------
# endpoint wiring: invalid contract → 422 contract_invalid
# --------------------------------------------------------------------------


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MSB_CONVERSATION_MODEL", "stub")
    monkeypatch.setenv("MSB_CONVERSATION_LEDGER_DIR", str(tmp_path))
    monkeypatch.setenv("MSB_CONVERSATION_GIT_HEAD", "testhead")
    monkeypatch.delenv("MCP_BRIDGE_SECRET", raising=False)
    from msb_v3.api.app import create_app

    return TestClient(create_app())


def test_endpoint_rejects_invalid_contract(client):
    r = client.post("/conversation/ask", json={
        "query": "run the plan", "mode": "workflow",
        "workflow": {
            "goal": "g",
            "dag": [{
                "task_id": "t1", "objective": "do the thing",
                "expected_output": {"predicates": [{"kind": "exit_code", "code": 0}]},  # exit-code only
            }],
        },
    })
    assert r.status_code == 422
    body = r.json()
    assert body["status"] == "error"
    assert body["error"]["code"] == "contract_invalid"


def test_endpoint_rejects_over_cap_dag(client):
    dag = [{
        "task_id": f"t{i}", "objective": f"o{i}", "skill": "retrieval",
        "parent": "root",
    } for i in range(tc.MAX_NODES_PER_LEVEL + 1)]
    r = client.post("/conversation/ask", json={
        "query": "run it", "mode": "workflow",
        "workflow": {"goal": "g", "dag": dag},
    })
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "contract_invalid"


def test_endpoint_accepts_valid_contract(client):
    r = client.post("/conversation/ask", json={
        "query": "run the plan", "mode": "workflow",
        "workflow": {
            "goal": "g",
            "dag": [{
                "task_id": "t1", "objective": "do the thing", "skill": "retrieval",
                "expected_output": {"predicates": [
                    {"kind": "file_exists", "path": "out/x.py"},
                    {"kind": "exit_code", "code": 0},
                ]},
            }],
        },
    })
    assert r.status_code == 200
    assert r.json()["mode"] == "workflow"


def test_endpoint_accepts_legacy_dag_entries(client):
    # legacy {skill, args} without task_id stays valid (unverified form)
    r = client.post("/conversation/ask", json={
        "query": "chatty workflow", "mode": "workflow",
        "workflow": {"goal": "g", "dag": [{"skill": "retrieval", "args": {"q": "x"}}]},
    })
    assert r.status_code == 200
