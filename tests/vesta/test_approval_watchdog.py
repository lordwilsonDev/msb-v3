from __future__ import annotations

from pathlib import Path

import pytest

from msb_v3.governance.killswitch import KillSwitch
from msb_v3.node.filesystem import FileWriter
from msb_v3.uac.audit_chain import AuditChain
from msb_v3.vesta.approval_watchdog import ApprovalWatchdog
from msb_v3.vesta.approvals import VestaApprovalStore
from msb_v3.vesta.evidence import EvidenceStore
from msb_v3.vesta.models import VestaFileWriteRequest
from msb_v3.vesta.runtime import VestaTaskStore
from msb_v3.vesta.write import VestaWriteService


def make_service(tmp_path: Path) -> VestaWriteService:
    audit = AuditChain(str(tmp_path / "audit.db"))
    tasks = VestaTaskStore(str(tmp_path / "tasks.db"))
    evidence = EvidenceStore(str(tmp_path / "evidence"), str(tmp_path / "evidence.db"))
    approvals = VestaApprovalStore(str(tmp_path / "tasks.db"))
    root = tmp_path / "sandbox"
    writer = FileWriter(root, max_bytes=100)
    kill = KillSwitch(str(tmp_path / "kill.db"), audit_chain=audit)
    return VestaWriteService(audit, tasks, evidence, approvals, writer, kill)


def make_watchdog(service: VestaWriteService) -> ApprovalWatchdog:
    return ApprovalWatchdog(
        approvals=service.approvals,
        tasks=service.tasks,
        audit=service.audit,
    )


def test_dangling_approval_is_voided_quarantined_and_audited(tmp_path: Path) -> None:
    """APPROVED approval with the task stuck pre-execution => VOID + QUARANTINE + audit."""
    service = make_service(tmp_path)
    pending = service.submit(VestaFileWriteRequest(path="hello.txt", content="hello"))
    # approval granted, but execution never ran (the cascade-harness crash case)
    service.approvals.approve(pending["approval_id"], "operator")
    assert service.tasks.get(pending["task_id"])["state"] == "WAITING_APPROVAL"
    assert not (tmp_path / "sandbox" / "hello.txt").exists()

    report = make_watchdog(service).run(operator="test-watchdog")

    assert report["scanned"] == 1
    assert [e["approval_id"] for e in report["dangling"]] == [pending["approval_id"]]
    assert [a["approval_id"] for a in report["voided"]] == [pending["approval_id"]]
    assert service.approvals.get(pending["approval_id"])["status"] == "VOID"
    assert service.tasks.get(pending["task_id"])["state"] == "QUARANTINED"
    assert not (tmp_path / "sandbox" / "hello.txt").exists()
    events = [r.event_type for r in service.audit.get_chain(component="vesta")]
    assert "approval.voided" in events
    void_event = next(r for r in service.audit.get_chain(component="vesta")
                      if r.event_type == "approval.voided")
    assert void_event.payload["source"] == "watchdog"
    assert void_event.payload["operator"] == "test-watchdog"


def test_completed_execution_is_left_alone(tmp_path: Path) -> None:
    """A legitimate success (task COMPLETED) must NOT be treated as dangling."""
    service = make_service(tmp_path)
    pending = service.submit(VestaFileWriteRequest(path="ok.txt", content="ok"))
    result = service.approve_and_execute(pending["approval_id"], "operator")
    assert result["status"] == "completed"
    assert service.tasks.get(pending["task_id"])["state"] == "COMPLETED"
    assert service.approvals.get(pending["approval_id"])["status"] == "APPROVED"

    report = make_watchdog(service).run()

    assert report["scanned"] == 1
    assert report["dangling"] == []
    assert report["in_flight"] == []
    assert len(report["ok"]) == 1
    assert report["voided"] == []
    # untouched
    assert service.approvals.get(pending["approval_id"])["status"] == "APPROVED"


def test_in_flight_task_is_reported_not_auto_voided(tmp_path: Path) -> None:
    """Recoverable states (EXECUTING) are recover_incomplete's job — flag only."""
    service = make_service(tmp_path)
    pending = service.submit(VestaFileWriteRequest(path="run.txt", content="run"))
    service.approvals.approve(pending["approval_id"], "operator")
    service.tasks.transition(pending["task_id"], "APPROVED")
    service.tasks.transition(pending["task_id"], "EXECUTING")

    report = make_watchdog(service).run()

    assert report["scanned"] == 1
    assert report["dangling"] == []
    assert [e["approval_id"] for e in report["in_flight"]] == [pending["approval_id"]]
    assert report["voided"] == []
    assert service.approvals.get(pending["approval_id"])["status"] == "APPROVED"


def test_dry_run_makes_no_changes(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    pending = service.submit(VestaFileWriteRequest(path="dry.txt", content="dry"))
    service.approvals.approve(pending["approval_id"], "operator")

    report = make_watchdog(service).run(dry_run=True)

    assert report["dry_run"] is True
    assert report["would_void"] == [pending["approval_id"]]
    assert "actions" not in report
    assert service.approvals.get(pending["approval_id"])["status"] == "APPROVED"
    assert service.tasks.get(pending["task_id"])["state"] == "WAITING_APPROVAL"


def test_run_is_idempotent_and_void_is_terminal(tmp_path: Path) -> None:
    """Second run finds nothing; the voided approval can never be re-decided."""
    from msb_v3.vesta.approvals import ApprovalError

    service = make_service(tmp_path)
    pending = service.submit(VestaFileWriteRequest(path="x.txt", content="x"))
    service.approvals.approve(pending["approval_id"], "operator")
    watchdog = make_watchdog(service)

    first = watchdog.run()
    assert len(first["voided"]) == 1

    second = watchdog.run()
    assert second["scanned"] == 0
    assert second["dangling"] == []

    with pytest.raises(ApprovalError, match="already decided"):
        service.approvals.approve(pending["approval_id"], "operator")


def test_orphan_approval_with_missing_task_is_voided(tmp_path: Path) -> None:
    """APPROVED approval whose task row is gone => void + explicit orphan note."""
    service = make_service(tmp_path)
    pending = service.submit(VestaFileWriteRequest(path="orphan.txt", content="o"))
    service.approvals.approve(pending["approval_id"], "operator")
    # simulate a lost task row (e.g., store restored from an older snapshot)
    with service.tasks._connect() as conn:
        conn.execute("DELETE FROM vesta_tasks WHERE task_id=?", (pending["task_id"],))

    report = make_watchdog(service).run()

    assert report["scanned"] == 1
    assert report["dangling"][0]["task_state"] == "MISSING"
    assert report["voided"][0]["approval_id"] == pending["approval_id"]
    assert service.approvals.get(pending["approval_id"])["status"] == "VOID"
