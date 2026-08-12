"""ApprovalQueue unit tests — nothing irreversible runs without approval."""

from __future__ import annotations

import pytest

from msb_v3.governance.approval import (
    APPROVAL_KINDS,
    ApprovalError,
    ApprovalQueue,
    IdempotencyError,
)
from msb_v3.uac.audit_chain import AuditChain


@pytest.fixture()
def queue(tmp_path) -> ApprovalQueue:
    return ApprovalQueue(
        db_path=str(tmp_path / "appr.db"),
        audit_chain=AuditChain(db_path=str(tmp_path / "audit.db")),
    )


def test_kinds_match_blueprint() -> None:
    assert set(APPROVAL_KINDS) == {
        "build", "combine", "promote_knowledge", "git_commit", "vault_write",
    }


def test_submit_and_approve(queue: ApprovalQueue) -> None:
    item = queue.submit(
        "build",
        "Stand up flywheel stage 7",
        payload={"module": "flywheel"},
        evidence_refs=["runtime/research/foo_UIM.json"],
    )
    assert item.status == "PENDING"
    got = queue.get(item.item_id)
    assert got.kind == "build"
    assert got.evidence_refs == ["runtime/research/foo_UIM.json"]

    decided = queue.approve(item.item_id, operator="wilson")
    assert decided.status == "APPROVED"
    assert decided.decided_by == "wilson"
    assert queue.pending() == []


def test_decisions_audited(queue: ApprovalQueue, tmp_path) -> None:
    item = queue.submit("vault_write", "promote harvest inbox")
    queue.reject(item.item_id, operator="wilson", reason="junk")
    chain = AuditChain(db_path=str(tmp_path / "audit.db"))
    records = chain.get_chain(component="approval")
    assert [r.event_type for r in records] == ["submitted", "rejected"]


def test_double_approve_raises(queue: ApprovalQueue) -> None:
    item = queue.submit("git_commit", "commit harvest")
    queue.approve(item.item_id, operator="wilson")
    with pytest.raises(IdempotencyError):
        queue.approve(item.item_id, operator="wilson")


def test_reject_requires_reason(queue: ApprovalQueue) -> None:
    item = queue.submit("combine", "cross-domain combine")
    with pytest.raises(ApprovalError):
        queue.reject(item.item_id, operator="wilson")


def test_cancel_transitions(queue: ApprovalQueue) -> None:
    item = queue.submit("promote_knowledge", "promote axiom")
    cancelled = queue.cancel(item.item_id, operator="wilson", reason="superseded")
    assert cancelled.status == "CANCELLED"


def test_unknown_item_raises(queue: ApprovalQueue) -> None:
    with pytest.raises(ApprovalError):
        queue.approve("nope", operator="wilson")


def test_unknown_kind_refused(queue: ApprovalQueue) -> None:
    with pytest.raises(ValueError):
        queue.submit("explode", "not a gated kind")


def test_restart_survival(tmp_path) -> None:
    p = str(tmp_path / "appr.db")
    a = ApprovalQueue(db_path=p, audit_chain=AuditChain(db_path=str(tmp_path / "audit.db")))
    a.submit("build", "survives restart")
    b = ApprovalQueue(db_path=p, audit_chain=AuditChain(db_path=str(tmp_path / "audit.db")))
    pending = b.pending()
    assert len(pending) == 1
    assert pending[0].title == "survives restart"


def test_audit_failure_surfaced_on_submit_and_decide(queue: ApprovalQueue, tmp_path) -> None:
    # Break the audit chain's DB after construction: the decision must still
    # land, but the failed audit write must be visible on the returned item
    # (never a black box), not silently dropped.
    audit_db = tmp_path / "audit.db"
    audit_db.unlink()
    audit_db.mkdir()
    item = queue.submit("build", "audit chain is broken")
    assert item.audit_failed is not None
    decided = queue.approve(item.item_id, operator="wilson")
    assert decided.status == "APPROVED"
    assert decided.audit_failed is not None
