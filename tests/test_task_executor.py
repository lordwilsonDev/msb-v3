"""Tests for the task contract executor + task producer (docs/task-contract-v1.md §3–§8).

The executor makes the central invariant machine-checked: exit 0 ≠ VERIFIED —
a task that exits 0 with failing predicates is FAILED and the ledger records
it as such; a task with no expected_output can never be VERIFIED.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from msb_v3.conversation import executor, task_producer  # noqa: E402
from msb_v3.conversation.task_contract import (  # noqa: E402
    claim_done_task,
    claim_ok_task,
)


def _contract(task_id="t.1", **over):
    base = {
        "task_id": task_id, "objective": f"objective {task_id}", "skill": "stub",
        "args": {}, "status": "READY",
    }
    base.update(over)
    return base


def _run(contract, tmp_path, ledger=None, runner=None, **kw):
    output = tmp_path / "out"
    output.mkdir(exist_ok=True)
    return executor.execute_contract(
        contract, runner=runner or executor.StubRunner(output), output_root=output,
        ledger_dir=ledger or tmp_path / "ledger", git_head="deadbeef", **kw,
    )


def test_exit_zero_with_passing_predicates_is_verified(tmp_path):
    c = _contract(
        args={"writes": [{"path": "out/retrieval.py", "content": "def search(): return 1"}]},
        side_effects=[{"kind": "file_write", "path": "out/**"}],
        allowed_tools=["filesystem"],
        expected_output={"schema": {}, "predicates": [
            {"kind": "file_exists", "path": "out/retrieval.py"},
            {"kind": "file_contains", "path": "out/retrieval.py", "text": "def search"},
        ]},
    )
    r = _run(c, tmp_path)
    assert r.status == "VERIFIED"
    assert r.artifact["polarity"] == "SUPPORTING"
    assert r.artifact["evidence_type"] == "task_verified"
    assert r.claim_id.startswith("claim:done:task:")
    assert r.verdict == "VERIFIED"
    assert r.event_ref is None  # no TASK_FAILED on success


def test_exit_zero_does_not_mean_verified(tmp_path):
    """THE central invariant: exit 0 + failing predicates ⇒ FAILED, and the
    ledger records it as a contradiction of claim:done:task."""
    c = _contract(task_id="t.2", args={"exit_code": 0},
                  expected_output={"schema": {}, "predicates": [
                      {"kind": "file_exists", "path": "out/never-written.py"},
                  ]})
    r = _run(c, tmp_path)
    assert r.status == "FAILED"
    assert r.failure_kind == "predicates"
    assert r.artifact["polarity"] == "CONTRADICTING"
    assert r.artifact["evidence_type"] == "task_failed_verify"
    assert r.claim_id.startswith("claim:done:task:")
    assert r.event_ref is not None and "task_events.jsonl" in r.event_ref


def test_no_expected_output_never_verified(tmp_path):
    c = _contract(task_id="t.5", args={"exit_code": 0})
    r = _run(c, tmp_path)
    assert r.status == "SUBMITTED"
    assert r.artifact["polarity"] == "INCONCLUSIVE"
    assert r.artifact["evidence_type"] == "task_submitted"
    assert r.verdict == "UNVERIFIED"
    # claim:done hash is over the contract incl. empty expected_output
    assert r.claim_id == claim_done_task("t.5", None, [])


def test_permission_envelope_tool_denial(tmp_path):
    c = _contract(task_id="t.3", allowed_tools=["filesystem"], args={"tools_used": ["network"]})
    r = _run(c, tmp_path)
    assert r.status == "FAILED" and r.failure_kind == "permission"
    assert "network" in r.reason
    assert r.claim_id == claim_ok_task("t.3")  # availability claim, CONTRADICTING
    assert r.artifact["evidence_type"] == "task_failed"


def test_permission_envelope_data_denial(tmp_path):
    c = _contract(task_id="t.3b", allowed_data=["tenant:alpha"], args={"data_accessed": ["tenant:beta"]})
    r = _run(c, tmp_path)
    assert r.status == "FAILED" and r.failure_kind == "permission"
    assert "tenant:beta" in r.reason


def test_fail_closed_no_tools(tmp_path):
    """Absent allowed_tools ⇒ [] ⇒ any reported tool use is a violation."""
    c = _contract(task_id="t.3c", args={"tools_used": ["filesystem"]})
    r = _run(c, tmp_path)
    assert r.status == "FAILED" and r.failure_kind == "permission"


def test_side_effect_scope_violation(tmp_path):
    c = _contract(task_id="t.4", side_effects=[{"kind": "file_write", "path": "out/**"}],
                  args={"artifacts": ["/etc/passwd"]})
    r = _run(c, tmp_path)
    assert r.status == "FAILED" and r.failure_kind == "scope"
    assert "side-effect scope" in r.reason


def test_side_effect_scope_fail_closed_when_none_declared(tmp_path):
    c = _contract(task_id="t.4b", args={"artifacts": ["out/x.txt"]})
    r = _run(c, tmp_path)
    assert r.status == "FAILED" and r.failure_kind == "scope"


def test_side_effect_declaration_escaping_fails_closed(tmp_path):
    """A malformed side-effect declaration must never widen the envelope:
    "" (silent whole-root), "/" (absolute) or ".." (parent) ⇒ every artifact
    is a violation — fail closed, never "everything allowed" (reviewer
    finding). A deliberate "**" stays valid (bounded to the output root)."""
    for label, bad, msg in [
        ("empty", "", "empty path"),
        ("absolute", "/", "absolute"),
        ("parent", "..", "outside the output root"),
    ]:
        c = _contract(task_id=f"t.esc.{label}",
                      side_effects=[{"kind": "file_write", "path": bad}],
                      args={"artifacts": ["out/x.txt"]})
        r = _run(c, tmp_path)
        assert r.status == "FAILED" and r.failure_kind == "scope", (label, r.reason)
        assert msg in r.reason

    # deliberate whole-root declarations stay VALID (bounded to output root)
    ok = _contract(task_id="t.esc.whole", side_effects=[{"kind": "file_write", "path": "**"}],
                   args={"artifacts": ["out/x.txt"]})
    assert _run(ok, tmp_path).status in ("SUBMITTED", "VERIFIED", "FAILED")  # not a scope failure
    assert _run(ok, tmp_path).failure_kind != "scope"


def test_symlink_escape_is_scope_violation(tmp_path):
    """An artifact that is a symlink out of the output root resolves outside
    it — must be a scope violation, not silently accepted."""
    output = tmp_path / "out"
    output.mkdir(exist_ok=True)
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    (output / "link").symlink_to(outside)
    c = _contract(task_id="t.esc.sym",
                  side_effects=[{"kind": "file_write", "path": "**"}],
                  args={"artifacts": ["link"]})
    r = _run(c, tmp_path)
    assert r.status == "FAILED" and r.failure_kind == "scope", r.reason


def test_data_default_scope_is_tenant_only(tmp_path):
    """Absent allowed_data ⇒ the request tenant only — any other tenant's
    data is a denial (fail-closed default)."""
    c = _contract(task_id="t.3d", args={"data_accessed": ["tenant:beta"]})
    r = _run(c, tmp_path)
    assert r.status == "FAILED" and r.failure_kind == "permission"
    assert "tenant:beta" in r.reason


def test_symlink_escape_blocked_before_rollback(tmp_path):
    """A symlink out of the output root is caught by the scope check BEFORE
    any rollback can run — the target must survive untouched. (The envelope
    blocks the escape earlier than rollback confirmation ever would.)"""
    output = tmp_path / "out"
    output.mkdir(exist_ok=True)
    outside = tmp_path / "secret.txt"
    outside.write_text("secret")
    (output / "junk").symlink_to(outside)
    c = _contract(
        task_id="t.esc.symdel",
        args={"exit_code": 0, "artifacts": ["junk"]},
        side_effects=[{"kind": "file_write", "path": "**"}],
        rollback={"kind": "file_delete", "scope": "out/"},
        expected_output={"schema": {}, "predicates": [
            {"kind": "file_exists", "path": "out/real.txt"},
        ]},
    )
    r = _run(c, tmp_path)
    assert r.status == "FAILED" and r.failure_kind == "scope", r.reason
    assert "outside declared side-effect scope" in r.reason
    assert outside.exists(), "symlink target must never be deleted"
    assert (output / "junk").exists()


def test_budget_cap_enforced(tmp_path):
    c = _contract(task_id="t.6", constraints={"budget_cap_usd": 0.01}, args={"cost_usd": 0.5})
    r = _run(c, tmp_path)
    assert r.status == "FAILED" and r.failure_kind == "budget"


def test_max_steps_enforced(tmp_path):
    c = _contract(task_id="t.7", constraints={"max_steps": 3}, args={"steps": 9})
    r = _run(c, tmp_path)
    assert r.status == "FAILED" and r.failure_kind == "steps"


def test_crash_exit_code_fails(tmp_path):
    c = _contract(task_id="t.8", args={"exit_code": 3})
    r = _run(c, tmp_path)
    assert r.status == "FAILED" and r.failure_kind == "crash"
    assert r.claim_id == claim_ok_task("t.8")


def test_runner_crash_is_failed(tmp_path):
    def boom(_contract):
        raise RuntimeError("boom")

    c = _contract(task_id="t.9")
    r = _run(c, tmp_path, runner=boom)
    assert r.status == "FAILED" and r.failure_kind == "crash"
    assert "boom" in r.reason


def test_rollback_file_delete_confirmed(tmp_path):
    output = tmp_path / "out"
    output.mkdir(exist_ok=True)
    c = _contract(
        task_id="t.10",
        args={"writes": [{"path": "out/junk.txt", "content": "x"}],
              "artifacts": ["out/junk.txt"]},
        side_effects=[{"kind": "file_write", "path": "out/**"}],
        rollback={"kind": "file_delete", "scope": "out/"},
        expected_output={"schema": {}, "predicates": [
            {"kind": "file_exists", "path": "out/real.txt"},
        ]},
    )
    r = _run(c, tmp_path)
    assert r.status == "ROLLED_BACK"
    assert not (output / "out" / "junk.txt").exists(), "file_delete must confirm removal"
    assert r.artifact["polarity"] == "CONTRADICTING"
    assert r.claim_id.startswith("claim:ok:task:")
    assert "rollback" in r.reason


def test_rollback_confirmation_failure_is_incident(tmp_path):
    """git_revert with no pre-task HEAD (not a git repo) ⇒ FAILED with
    rollback_failed — never a false ROLLED_BACK."""
    c = _contract(task_id="t.11", args={"exit_code": 0},
                  rollback={"kind": "git_revert", "scope": "out/"},
                  expected_output={"schema": {}, "predicates": [
                      {"kind": "file_exists", "path": "out/x.txt"},
                  ]})
    r = _run(c, tmp_path)
    assert r.status == "FAILED"
    assert r.failure_kind == "rollback_failed"
    assert r.event_ref is not None


def test_git_revert_confirmed_in_real_repo(tmp_path):
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"], check=True)
    (repo / "base.txt").write_text("base")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
    pre = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                         capture_output=True, text=True, check=True).stdout.strip()

    # the task writes + commits junk (as the runner hook would), then fails
    # predicates; rollback git_revert must confirm HEAD restored + scope clean
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "task work", "--allow-empty"], check=True)
    output = repo
    c = _contract(task_id="t.12", args={"exit_code": 0},
                  rollback={"kind": "git_revert", "scope": "out/"},
                  expected_output={"schema": {}, "predicates": [
                      {"kind": "exit_code", "code": 0},
                      {"kind": "file_exists", "path": "out/x.txt"},
                  ]})
    ledger = tmp_path / "ledger"
    r = executor.execute_contract(
        c, runner=executor.StubRunner(output), output_root=output,
        ledger_dir=ledger, git_head="deadbeef", pre_task_head=pre,
    )
    assert r.status == "ROLLED_BACK", r.reason  # restore + confirm succeeded — the workspace is back to pre-task
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()
    assert head == pre, "git_revert must restore the pre-task HEAD"


def test_select_ready_task_respects_preconditions():
    dag = [
        _contract(task_id="a", status="VERIFIED"),
        _contract(task_id="b", preconditions=["a:VERIFIED"]),
        _contract(task_id="c", status="READY", preconditions=["b:VERIFIED"]),
    ]
    picked = executor.select_ready_task(dag)
    assert picked is not None and picked["task_id"] == "b"
    assert executor.select_ready_task([]) is None


def test_advance_dag_one_node_per_call(tmp_path):
    dag = [
        _contract(task_id="x", status="READY", args={"exit_code": 0, "output": "done"},
                  expected_output={"schema": {}, "predicates": [
                      {"kind": "exit_code", "code": 0},
                      {"kind": "output_equals", "expected": "done"},
                  ]}),
        _contract(task_id="y", status="READY", preconditions=["x:VERIFIED"]),
    ]
    output = tmp_path / "out"
    output.mkdir(exist_ok=True)
    ledger = tmp_path / "ledger"

    dag2, r1 = executor.advance_dag(dag, runner=executor.StubRunner(output),
                                    output_root=output, ledger_dir=ledger, git_head="deadbeef")
    assert r1 is not None and r1.status == "VERIFIED"
    assert {e.get("task_id"): e.get("status") for e in dag2} == {"x": "VERIFIED", "y": "READY"}

    dag3, r2 = executor.advance_dag(dag2, runner=executor.StubRunner(output),
                                    output_root=output, ledger_dir=ledger, git_head="deadbeef")
    assert r2 is not None and r2.status == "SUBMITTED"  # y has no expected_output
    assert dag3[1]["status"] == "SUBMITTED"

    dag4, r3 = executor.advance_dag(dag3, runner=executor.StubRunner(output),
                                    output_root=output, ledger_dir=ledger, git_head="deadbeef")
    assert r3 is None  # nothing READY left


def test_re_execution_is_idempotent(tmp_path):
    c = _contract(task_id="t.13", args={"exit_code": 0},
                  expected_output={"schema": {}, "predicates": [
                      {"kind": "exit_code", "code": 0},
                      {"kind": "output_equals", "expected": None},
                  ]})
    ledger = tmp_path / "ledger"
    r1 = _run(c, tmp_path, ledger=ledger)
    claims_before = len(task_producer.load_registry(ledger / "claims.json")["claims"])
    r2 = _run(c, tmp_path, ledger=ledger)
    claims_after = len(task_producer.load_registry(ledger / "claims.json")["claims"])
    assert r1.claim_id == r2.claim_id
    assert r1.evidence_ref == r2.evidence_ref
    assert claims_after == claims_before  # cursor dedupes


def test_ledger_artifacts_and_event_stream(tmp_path):
    ledger = tmp_path / "ledger"
    r = _run(_contract(task_id="t.14", args={"exit_code": 0},
                       expected_output={"schema": {}, "predicates": [
                           {"kind": "exit_code", "code": 0},
                           {"kind": "file_exists", "path": "out/missing.py"},
                       ]}), tmp_path, ledger=ledger)
    assert r.event_ref is not None
    event = json.loads(Path(r.event_ref).read_text(encoding="utf-8").strip().splitlines()[-1])
    assert event["event"] == "TASK_FAILED"
    assert event["task_id"] == "t.14"  # verbatim
    assert event["failed_step"] == "t.14"
    artifact = json.loads((ledger / r.evidence_ref.replace("ledger://", "")).read_text())
    # §8 shape: five provenance layers, record-derived git_head
    assert set(artifact["provenance"]) == {"execution", "environment", "input", "verifier", "dependency"}
    assert artifact["git_head"] == "deadbeef"
    assert artifact["result"] == "FAILED"


def test_invalid_contract_fails_fast(tmp_path):
    c = {"skill": "x", "args": {}}
    with pytest.raises(ValueError):
        _run(c, tmp_path)
    c2 = _contract(task_id="t.15", expected_output={"schema": {}, "predicates": [
        {"kind": "exit_code", "code": 0},
    ]})
    with pytest.raises(ValueError):  # exit_code-only predicates rejected at validation
        _run(c2, tmp_path)


def test_producer_self_test_and_executor_self_test_pass():
    assert task_producer.run_self_test() == 0
    assert executor.run_self_test() == 0
