"""META-0: the Meta-System contract types.

These are types, not behaviour — the tests pin the shape, the blueprint
enum vocabularies, the model-independence invariant (M1-M4, M12), and the
one derived gate (``VerificationResult.all_required_passed``, blueprint §12).
"""

from __future__ import annotations

import dataclasses

from msb_v3.meta import (
    MSL,
    MSL_VERSION,
    CheckResult,
    Complexity,
    FailureRecord,
    MetaTask,
    ProjectState,
    TaskState,
    Verdict,
    VerificationResult,
    WorkerResult,
    WorkerStatus,
)


def test_all_seven_contract_types_are_exported() -> None:
    for t in (MetaTask, MSL, VerificationResult, FailureRecord, WorkerResult):
        assert dataclasses.is_dataclass(t)
    for e in (TaskState, ProjectState):
        assert issubclass(e, str)  # str-enum: JSON/ledger friendly


def test_task_state_is_the_blueprint_six_plus_terminals() -> None:
    assert {s.value for s in TaskState} == {
        "READY", "BLOCKED", "RUNNING", "VERIFYING",
        "PASSED", "FAILED", "RETRYING", "ESCALATED",
    }


def test_project_state_covers_intake_through_release() -> None:
    values = [s.value for s in ProjectState]
    assert values[0] == "INTAKE" and values[-1] == "RELEASE"
    assert "VERIFICATION" in values and "DECOMPOSITION" in values


def test_verdict_has_explicit_expected_skip_not_a_silent_pass() -> None:
    assert {v.value for v in Verdict} == {"PASS", "FAIL", "EXPECTED_SKIP"}


def test_worker_status_distinguishes_no_change_from_error() -> None:
    assert WorkerStatus.NO_CHANGE is not WorkerStatus.ERROR
    assert {s.value for s in WorkerStatus} == {"PRODUCED", "NO_CHANGE", "ERROR"}


def test_metatask_is_model_independent() -> None:
    """A task constructs with no model named; ``assigned_model`` is an
    optional annotation that defaults to None (M12)."""
    t = MetaTask(task_id="TASK-0001", objective="Implement ProviderContract v1")
    assert t.assigned_model is None
    assert t.state is TaskState.BLOCKED
    assert t.dependencies == [] and t.children == []
    fields = {f.name for f in dataclasses.fields(MetaTask)}
    assert "model" not in fields and "provider" not in fields


def test_metatask_carries_recursive_decomposition_links() -> None:
    parent = MetaTask(task_id="T-100", objective="big", children=["T-100a", "T-100b"])
    child = MetaTask(task_id="T-100a", objective="smaller", parent_id="T-100")
    assert child.parent_id == parent.task_id
    assert child.task_id in parent.children


def test_msl_is_versioned_and_traces_back_to_a_task() -> None:
    m = MSL(msl_id="MSL-1", source_task_id="TASK-0001", objective="Implement X")
    assert m.msl_version == MSL_VERSION == "v1"
    assert m.source_task_id == "TASK-0001"
    assert m.max_attempts == 3 and m.escalation == "replan"
    fields = {f.name for f in dataclasses.fields(MSL)}
    assert "model" not in fields and "prompt" not in fields  # MSL is not a prompt


def test_complexity_tiers_drive_routing() -> None:
    assert [c.value for c in Complexity] == ["LOW", "MEDIUM", "HIGH", "CRITICAL"]


def test_verification_all_required_passed_gate() -> None:
    ok = VerificationResult(
        task_id="T1", verdict=Verdict.PASS,
        checks=[CheckResult(name="pytest", passed=True),
                CheckResult(name="ruff", passed=True)],
        metrics={"multi_turn_task_success": 1.0},
    )
    assert ok.all_required_passed is True

    pass_but_check_failed = VerificationResult(
        task_id="T1", verdict=Verdict.PASS,
        checks=[CheckResult(name="pytest", passed=False, detail="1 failed")],
    )
    assert pass_but_check_failed.all_required_passed is False

    failed = VerificationResult(task_id="T1", verdict=Verdict.FAIL)
    assert failed.all_required_passed is False


def test_verification_metrics_use_eval_flywheel_ids() -> None:
    v = VerificationResult(
        task_id="T1", verdict=Verdict.PASS,
        metrics={"multi_turn_trajectory_quality": 0.8, "final_response_match": 1.0},
    )
    assert set(v.metrics) <= {
        "multi_turn_task_success", "multi_turn_trajectory_quality",
        "multi_turn_tool_use_quality", "multi_turn_general_quality",
        "final_response_quality", "final_response_match",
        "final_response_reference_free", "tool_use_quality",
        "general_quality", "instruction_following",
    }


def test_failure_record_is_repair_ready() -> None:
    f = FailureRecord(
        failure_id="FAIL-0087",
        task_id="TASK-0172",
        symptom="capability declaration missing",
        evidence=["tests/providers/test_capabilities.py:41"],
        likely_causes=["adapter omitted capability", "contract impl incomplete"],
        recommended_action="inspect ProviderContract v1",
        repair_scope=["src/msb_v3/providers/"],
    )
    assert f.retry_allowed is True          # default
    assert f.cluster_id is None             # set later by loss clustering
    assert f.repair_scope == ["src/msb_v3/providers/"]


def test_worker_result_is_raw_outcome_not_a_verdict() -> None:
    w = WorkerResult(task_id="T1", worker_id="worker-a", status=WorkerStatus.PRODUCED,
                     artifact_ref="/tmp/wt/patch.diff", tokens_out=1200)
    assert w.attempt == 1
    assert not hasattr(w, "verdict")        # correctness is VerificationResult's job
