from __future__ import annotations

from pathlib import Path

import pytest

from msb_v3.governance.killswitch import KillSwitch
from msb_v3.node.filesystem import FileWriter
from msb_v3.uac.audit_chain import AuditChain
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


def test_file_write_requires_approval_then_verifies_and_audits(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    pending = service.submit(VestaFileWriteRequest(path="hello.txt", content="hello"))
    assert pending["status"] == "approval_required"
    assert not (tmp_path / "sandbox" / "hello.txt").exists()

    result = service.approve_and_execute(pending["approval_id"], "operator")
    assert result["status"] == "completed"
    assert (tmp_path / "sandbox" / "hello.txt").read_text() == "hello"
    assert len(result["evidence_refs"]) == 4
    assert service.tasks.get(pending["task_id"])["state"] == "COMPLETED"
    assert service.audit.verify_chain()["valid"] is True


def test_rejection_never_writes(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    pending = service.submit(VestaFileWriteRequest(path="rejected.txt", content="no"))
    result = service.reject(pending["approval_id"], "operator", "not needed")
    assert result["status"] == "rejected"
    assert service.tasks.get(pending["task_id"])["state"] == "DENIED"
    assert not (tmp_path / "sandbox" / "rejected.txt").exists()


def test_precondition_scope_or_kill_failure_quarantines_without_write(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    existing = tmp_path / "sandbox" / "existing.txt"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_text("original")
    pending = service.submit(
        VestaFileWriteRequest(
            path="existing.txt",
            content="replacement",
            expected_sha256="0" * 64,
        )
    )
    result = service.approve_and_execute(pending["approval_id"], "operator")
    assert result["status"] == "quarantined"
    assert existing.read_text() == "original"
    assert service.tasks.get(pending["task_id"])["state"] == "QUARANTINED"
    # The APPROVED approval must not be left dangling next to a quarantined task.
    assert service.approvals.get(pending["approval_id"])["status"] == "VOID"
    assert any(record.event_type == "approval.voided" for record in service.audit.get_chain(component="vesta"))

    killed = service.submit(VestaFileWriteRequest(path="killed.txt", content="blocked"))
    service.kill_switch.arm("test", "incident")
    blocked = service.approve_and_execute(killed["approval_id"], "operator")
    assert blocked["status"] == "quarantined"
    assert not (tmp_path / "sandbox" / "killed.txt").exists()
    assert service.approvals.get(killed["approval_id"])["status"] == "VOID"


def test_write_approval_void_is_terminal(tmp_path: Path) -> None:
    from msb_v3.vesta.approvals import ApprovalError

    service = make_service(tmp_path)
    pending = service.submit(VestaFileWriteRequest(path="x.txt", content="x"))
    store = service.approvals
    with pytest.raises(ApprovalError, match="APPROVED"):
        store.void(pending["approval_id"])  # PENDING cannot be voided
    store.approve(pending["approval_id"], "operator")
    store.void(pending["approval_id"], "precondition failed")
    assert store.get(pending["approval_id"])["status"] == "VOID"
    with pytest.raises(ApprovalError, match="already decided"):
        store.approve(pending["approval_id"], "operator")
    with pytest.raises(ApprovalError, match="already decided"):
        store.reject(pending["approval_id"], "operator")
