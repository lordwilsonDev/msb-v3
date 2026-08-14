"""Service-layer approval-bypass suite (MSB-GOV-EVAL-001 V6 coverage).

Attempts to defeat `VestaWriteService.approve_and_execute` — the REAL
protected-mutation path — through approval-ledger manipulation. Every test
verifies ACTUAL STATE (target file existence/content, task state, approval
state, audit chain), not return codes. An attacker with application-level
DB access (the stated threat model) forges the ledger; the defense must hold
at the service layer (approval status machine, approval-pinned payload hash,
filesystem sandbox).

Attack classes:
  - expired A-BIND             : bind deadline in the past -> approval EXPIRED
  - revoked approval           : REJECTED / VOIDED -> cannot re-decide
  - unknown approval id        : forged id -> cannot resolve
  - forged evidence blob       : swapped payload bytes -> hash verification fails
  - forged evidence + DB hash  : blob AND evidence-DB sha256 forged to match ->
                                 approval-pinned payload_sha256 (separate DB) catches it
  - forged target path         : approval row repointed outside the sandbox ->
                                 FileWriter blocks the escape
  - double execution           : one approval executed twice -> second is denied
  - forged PENDING status      : ledger rewritten to re-open a decided approval ->
                                 the filesystem sandbox still blocks escapes
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from msb_v3.governance.killswitch import KillSwitch
from msb_v3.node.filesystem import FileWriter
from msb_v3.uac.audit_chain import AuditChain
from msb_v3.vesta.approvals import ApprovalError, VestaApprovalStore
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


def sandbox_file(service: VestaWriteService, name: str) -> Path:
    return service.writer.root / name


def _tamper_approval(service: VestaWriteService, approval_id: str, **fields) -> None:
    """Rewrite approval-ledger columns directly — attacker with DB access."""
    sets = ", ".join(f"{k}=?" for k in fields)
    with sqlite3.connect(service.approvals.db_path) as conn:
        conn.execute(
            f"UPDATE vesta_approvals SET {sets} WHERE approval_id=?",
            (*fields.values(), approval_id),
        )


def _forge_evidence_blob(service: VestaWriteService, evidence_id: str,
                         forged: bytes, also_fix_db_hash: bool) -> None:
    meta = service.evidence.get(evidence_id)
    blob = service.evidence.root / meta["relative_path"]
    blob.write_bytes(forged)
    if also_fix_db_hash:
        digest = hashlib.sha256(forged).hexdigest()
        with sqlite3.connect(service.evidence.db_path) as conn:
            conn.execute(
                "UPDATE vesta_evidence SET sha256=?, size_bytes=? WHERE evidence_id=?",
                (digest, len(forged), evidence_id),
            )


# ── Expired A-BIND ────────────────────────────────────────────────────────────
def test_expired_bind_cannot_execute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bind deadline in the past => the approval is EXPIRED; execution denied."""
    import msb_v3.vesta.write as write_mod
    from msb_v3.vesta.models import ABind

    class ExpiredBind:
        @staticmethod
        def create(session_id, capabilities, **kwargs):
            kwargs["ttl_seconds"] = 0  # deadline = now -> already expired
            return ABind.create(session_id, capabilities, **kwargs)

    monkeypatch.setattr(write_mod, "ABind", ExpiredBind)
    service = make_service(tmp_path)
    pending = service.submit(VestaFileWriteRequest(path="late.txt", content="late"))

    with pytest.raises(ApprovalError, match="expired"):
        service.approve_and_execute(pending["approval_id"], "operator")

    assert service.approvals.get(pending["approval_id"])["status"] == "EXPIRED"
    assert not sandbox_file(service, "late.txt").exists()
    assert service.tasks.get(pending["task_id"])["state"] == "WAITING_APPROVAL"


# ── Revoked / decided approvals ───────────────────────────────────────────────
def test_rejected_approval_cannot_execute(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    pending = service.submit(VestaFileWriteRequest(path="rej.txt", content="r"))
    service.reject(pending["approval_id"], "operator", "not needed")

    with pytest.raises(ApprovalError, match="already decided"):
        service.approve_and_execute(pending["approval_id"], "operator")

    assert not sandbox_file(service, "rej.txt").exists()
    assert service.tasks.get(pending["task_id"])["state"] == "DENIED"


def test_voided_approval_cannot_execute(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    pending = service.submit(VestaFileWriteRequest(path="void.txt", content="v"))
    service.approvals.approve(pending["approval_id"], "operator")
    service.approvals.void(pending["approval_id"], "precondition failed")

    with pytest.raises(ApprovalError, match="already decided"):
        service.approve_and_execute(pending["approval_id"], "operator")

    assert not sandbox_file(service, "void.txt").exists()


def test_unknown_approval_id_cannot_execute(tmp_path: Path) -> None:
    service = make_service(tmp_path)
    with pytest.raises(ApprovalError, match="unknown approval"):
        service.approve_and_execute("ack_forged_id", "operator")


# ── Forged evidence ───────────────────────────────────────────────────────────
def test_forged_evidence_blob_is_quarantined(tmp_path: Path) -> None:
    """Blob swapped => evidence hash verification fails => no mutation, VOID."""
    service = make_service(tmp_path)
    pending = service.submit(VestaFileWriteRequest(path="doc.txt", content="original"))
    payload_evidence_id = service.approvals.get(pending["approval_id"])["payload_evidence_id"]
    _forge_evidence_blob(service, payload_evidence_id, b"forged payload", also_fix_db_hash=False)

    result = service.approve_and_execute(pending["approval_id"], "operator")

    assert result["status"] == "quarantined"
    assert not sandbox_file(service, "doc.txt").exists()
    assert service.approvals.get(pending["approval_id"])["status"] == "VOID"
    assert service.tasks.get(pending["task_id"])["state"] == "QUARANTINED"
    assert service.audit.verify_chain()["valid"] is True


def test_forged_evidence_with_fixed_db_hash_is_still_caught(tmp_path: Path) -> None:
    """Blob AND evidence-DB sha256 forged to match => the approval row's
    payload_sha256 (a SEPARATE ledger) pins the original content => no mutation."""
    service = make_service(tmp_path)
    pending = service.submit(VestaFileWriteRequest(path="doc.txt", content="original"))
    payload_evidence_id = service.approvals.get(pending["approval_id"])["payload_evidence_id"]
    _forge_evidence_blob(service, payload_evidence_id, b"forged payload", also_fix_db_hash=True)

    result = service.approve_and_execute(pending["approval_id"], "operator")

    assert result["status"] == "quarantined"
    assert "payload" in result["error"]
    assert not sandbox_file(service, "doc.txt").exists()
    assert service.approvals.get(pending["approval_id"])["status"] == "VOID"


# ── Forged target path ────────────────────────────────────────────────────────
def test_forged_target_path_cannot_escape_sandbox(tmp_path: Path) -> None:
    """Approval row repointed outside the sandbox => FileWriter blocks escape."""
    service = make_service(tmp_path)
    pending = service.submit(VestaFileWriteRequest(path="doc.txt", content="escape me"))
    _tamper_approval(service, pending["approval_id"], target_path="../escape.txt")

    result = service.approve_and_execute(pending["approval_id"], "operator")

    assert result["status"] == "quarantined"
    escape = tmp_path / "escape.txt"
    assert not escape.exists()  # the write never left the sandbox
    assert not sandbox_file(service, "doc.txt").exists()
    assert service.approvals.get(pending["approval_id"])["status"] == "VOID"


# ── Double execution ──────────────────────────────────────────────────────────
def test_double_execution_is_denied(tmp_path: Path) -> None:
    """One approval can authorize exactly one execution."""
    service = make_service(tmp_path)
    pending = service.submit(VestaFileWriteRequest(path="once.txt", content="once"))
    first = service.approve_and_execute(pending["approval_id"], "operator")
    assert first["status"] == "completed"
    assert sandbox_file(service, "once.txt").read_text() == "once"

    with pytest.raises(ApprovalError, match="already decided"):
        service.approve_and_execute(pending["approval_id"], "operator")

    # state unchanged by the second attempt
    assert sandbox_file(service, "once.txt").read_text() == "once"
    assert service.tasks.get(pending["task_id"])["state"] == "COMPLETED"


# ── Forged status re-open ─────────────────────────────────────────────────────
def test_forged_pending_status_cannot_escape_sandbox(tmp_path: Path) -> None:
    """An attacker who rewrites the ledger to re-open a decided approval can
    trigger an execution attempt — but the filesystem sandbox still blocks any
    path outside its root. Documented as a trust-model note: a DB-level
    attacker can forge the ledger itself; the sandbox is the last line."""
    service = make_service(tmp_path)
    pending = service.submit(VestaFileWriteRequest(path="doc.txt", content="x"))
    service.reject(pending["approval_id"], "operator", "denied")
    _tamper_approval(service, pending["approval_id"],
                     status="PENDING", decided_at=None, decided_by=None, reason=None,
                     target_path="../escape.txt")

    result = service.approve_and_execute(pending["approval_id"], "operator")

    # the re-opened approval executes into a CapabilityViolation -> quarantine
    assert result["status"] == "quarantined"
    assert not (tmp_path / "escape.txt").exists()  # sandbox held
    assert service.approvals.get(pending["approval_id"])["status"] == "VOID"
