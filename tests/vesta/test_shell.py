from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from msb_v3.governance.killswitch import KillSwitch
from msb_v3.uac.audit_chain import AuditChain
from msb_v3.vesta.evidence import EvidenceStore
from msb_v3.vesta.models import VestaShellRequest
from msb_v3.vesta.runtime import VestaTaskStore
from msb_v3.vesta.shell import (
    ShellCapabilityError,
    ShellExecutor,
    VestaShellApprovalStore,
    VestaShellService,
)


def make_service(tmp_path: Path) -> VestaShellService:
    audit = AuditChain(str(tmp_path / "audit.db"))
    tasks = VestaTaskStore(str(tmp_path / "tasks.db"))
    evidence = EvidenceStore(str(tmp_path / "evidence"), str(tmp_path / "evidence.db"))
    approvals = VestaShellApprovalStore(str(tmp_path / "tasks.db"))
    root = tmp_path / "sandbox"
    executor = ShellExecutor(root, timeout_s=1.0, max_output_bytes=128)
    return VestaShellService(
        audit,
        tasks,
        evidence,
        approvals,
        executor,
        KillSwitch(str(tmp_path / "kill.db"), audit_chain=audit),
    )


def test_shell_requires_exact_approval_and_records_output_evidence(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    pending = service.submit(
        VestaShellRequest(executable="echo", args=["SHELL_OK"], expected_stdout="SHELL_OK\n")
    )
    assert pending["status"] == "approval_required"
    assert not list((tmp_path / "sandbox").iterdir())

    result = service.approve_and_execute(pending["approval_id"], "operator")
    assert result["status"] == "completed"
    assert result["execution"]["stdout"] == "SHELL_OK\n"
    assert result["verification"]["ok"] is True
    assert len(result["evidence_refs"]) == 3
    assert service.tasks.get(pending["task_id"])["state"] == "COMPLETED"
    assert service.audit.verify_chain()["valid"] is True


def test_shell_rejects_unknown_commands_and_flags_before_execution(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    unknown = service.submit(VestaShellRequest(executable="sh", args=["-c", "echo BAD"]))
    assert unknown["status"] == "denied"
    assert service.tasks.get(unknown["task_id"])["state"] == "DENIED"

    flags = service.submit(VestaShellRequest(executable="echo", args=["-e", "BAD"]))
    assert flags["status"] == "denied"
    assert service.tasks.get(flags["task_id"])["state"] == "DENIED"


def test_shell_metacharacters_are_data_not_shell_code(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    marker = tmp_path / "x"
    payload = "$(touch x)"
    pending = service.submit(VestaShellRequest(executable="echo", args=[payload]))
    result = service.approve_and_execute(pending["approval_id"], "operator")
    assert result["status"] == "completed"
    assert payload in result["execution"]["stdout"]
    assert not marker.exists()


def test_shell_postcondition_mismatch_quarantines_and_voids_approval(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    pending = service.submit(VestaShellRequest(executable="echo", args=["actual"], expected_stdout="wrong\n"))
    result = service.approve_and_execute(pending["approval_id"], "operator")
    assert result["status"] == "quarantined"
    assert service.tasks.get(pending["task_id"])["state"] == "QUARANTINED"
    # The APPROVED approval must not be left dangling next to a quarantined task.
    assert service.approvals.get(pending["approval_id"])["status"] == "VOID"
    assert any(record.event_type == "approval.voided" for record in service.audit.get_chain(component="vesta"))


def test_shell_kill_switch_blocks_before_process_start_and_voids_approval(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    pending = service.submit(VestaShellRequest(executable="echo", args=["blocked"]))
    service.kill_switch.arm("test", "shell safety test")
    result = service.approve_and_execute(pending["approval_id"], "operator")
    assert result["status"] == "quarantined"
    assert result["error"] == "kill switch armed"
    assert service.tasks.get(pending["task_id"])["state"] == "QUARANTINED"
    assert service.approvals.get(pending["approval_id"])["status"] == "VOID"


def test_shell_approval_command_tampering_quarantines_without_execution(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    pending = service.submit(VestaShellRequest(executable="echo", args=["safe"]))
    with sqlite3.connect(tmp_path / "tasks.db") as conn:
        conn.execute(
            "UPDATE vesta_shell_approvals SET command_json=? WHERE approval_id=?",
            (json.dumps({"executable": "pwd", "args": [], "expected_stdout": None}), pending["approval_id"]),
        )
    result = service.approve_and_execute(pending["approval_id"], "operator")
    assert result["status"] == "quarantined"
    assert service.tasks.get(pending["task_id"])["state"] == "QUARANTINED"
    assert service.approvals.get(pending["approval_id"])["status"] == "VOID"


def test_shell_approval_void_is_terminal(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    pending = service.submit(VestaShellRequest(executable="echo", args=["x"]))
    store = service.approvals
    with pytest.raises(ShellCapabilityError, match="APPROVED"):
        store.void(pending["approval_id"])  # PENDING cannot be voided
    store.approve(pending["approval_id"], "operator")
    store.void(pending["approval_id"], "postcondition failed")
    assert store.get(pending["approval_id"])["status"] == "VOID"
    with pytest.raises(ShellCapabilityError, match="already decided"):
        store.approve(pending["approval_id"], "operator")
    with pytest.raises(ShellCapabilityError, match="already decided"):
        store.reject(pending["approval_id"], "operator")


def test_shell_executor_timeout_kills_process_group(tmp_path: Path) -> None:
    sleep_path = Path("/bin/sleep")
    if not sleep_path.exists():
        pytest.skip("sleep is unavailable")
    executor = ShellExecutor(
        tmp_path / "sandbox",
        allowed_commands={"sleep": str(sleep_path)},
        timeout_s=0.01,
        max_output_bytes=128,
    )
    result = executor.run("sleep", ["1"])
    assert result.timed_out is True
    assert result.returncode is None


def test_shell_executor_rejects_absolute_and_invalid_arguments(tmp_path: Path) -> None:
    executor = ShellExecutor(tmp_path / "sandbox")
    with pytest.raises(ShellCapabilityError, match="allowlisted"):
        executor.run("/bin/sh", ["-c", "echo BAD"])
    with pytest.raises(ShellCapabilityError, match="invalid"):
        executor.run("echo", ["bad\x00arg"])
