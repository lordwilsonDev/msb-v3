"""Phase 3 — RepairEngine: governed repair plans.

Pins the contract: plans carry risk/rollback/authority/verification; propose()
maps a diagnosis to candidates and never proposes prohibited classes
(chain_invalid etc.); OPERATOR plans require durable approval while AUTO
plans execute directly; execution is verify-before-trust → kill-switch gate →
apply → verification contract → rollback on failure, all audited.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from msb_ledger.audit_chain import AuditChain, tamper
from msb_v3.ops.discrepancy import (
    SEV_CRITICAL,
    SEV_WARN,
    Discrepancy,
    DiscrepancyStore,
)
from msb_v3.ops.repair import (
    AUTO,
    OPERATOR,
    STATUS_AWAITING,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_ROLLED_BACK,
    RepairService,
    RepairStore,
)
from msb_v3.wake.store import WakeStore


class _FakeKillSwitch:
    def __init__(self, armed: bool = False) -> None:
        self._armed = armed

    def is_armed(self) -> bool:
        return self._armed


@pytest.fixture()
def iso(tmp_path: Path) -> dict:
    """All stores pinned to tmp — never touches production data."""
    return {
        "store": RepairStore(db_path=str(tmp_path / "repairs.db")),
        "audit": AuditChain(db_path=str(tmp_path / "audit.db"), allow_keyless=True),
        "kill_switch": _FakeKillSwitch(),
        "disc": DiscrepancyStore(db_path=str(tmp_path / "disc.db")),
    }


def _service(iso: dict, **overrides) -> RepairService:
    return RepairService(
        store=iso["store"], audit=iso["audit"], kill_switch=iso["kill_switch"],
        discrepancy_store=iso["disc"], **overrides,
    )


def _wake(tmp_path: Path) -> WakeStore:
    from msb_v3.wake.store import default_db_path

    return WakeStore(db_path=str(default_db_path()))


# --- planning --------------------------------------------------------------


def test_propose_maps_provider_outage_to_requeue(iso: dict) -> None:
    diagnosis = {
        "roots": [{"resource": "deepseek", "kind": "provider_outage", "confidence": 0.95}],
        "signals": [],
    }
    report = _service(iso).propose(diagnosis=diagnosis)
    assert len(report["plans"]) == 1
    plan = report["plans"][0]
    assert plan["action"] == "requeue_wake"
    assert plan["required_authority"] == AUTO
    assert plan["risk"] == "low"
    assert plan["verification_contract"]["kind"] == "wake_requeued"
    assert plan["status"] == "proposed"
    # Persisted + audited.
    assert iso["store"].get(plan["plan_id"]).action == "requeue_wake"
    events = [r.event_type for r in iso["audit"].get_chain(component="repair")]
    assert "repair.proposed" in events


def test_propose_maps_backlog_to_quarantine(iso: dict) -> None:
    diagnosis = {
        "roots": [],
        "signals": [{"kind": "queue_backlog", "resource": "wake_inbox", "detail": "3 pending", "meta": {"pending": 3}}],
    }
    report = _service(iso).propose(diagnosis=diagnosis)
    assert [p["action"] for p in report["plans"]] == ["quarantine_wake"]
    assert report["plans"][0]["required_authority"] == OPERATOR
    # OPERATOR plans from propose() must be approvable, not stuck in proposed.
    assert report["plans"][0]["status"] == STATUS_AWAITING
    approved = _service(iso).approve(report["plans"][0]["plan_id"], operator="wilson")
    assert approved["status"] == "approved"


def test_propose_never_proposes_prohibited_classes(iso: dict) -> None:
    iso["disc"].insert(
        Discrepancy(
            id="d1", timestamp="2026-08-22T00:00:00+00:00", subsystem="audit_chain",
            expected_state="chain verifies", observed_state="broken",
            discrepancy_type="chain_invalid", severity=SEV_CRITICAL, evidence={},
            confidence=1.0, affected_resource="audit_chain.db", suggested_action="investigate",
        )
    )
    report = _service(iso).propose(diagnosis={"roots": [], "signals": []})
    assert report["plans"] == []
    assert any("chain_invalid" in s for s in report["prohibited"])


def test_submit_rejects_unknown_action(iso: dict) -> None:
    with pytest.raises(ValueError, match="unknown repair action"):
        _service(iso).submit("format_disk", params={})


def test_submit_manual_operator_plan_awaits_approval(iso: dict) -> None:
    plan = _service(iso).submit("quarantine_wake", params={"note": "manual"})
    assert plan["status"] == STATUS_AWAITING


# --- approval + execution --------------------------------------------------


def test_execute_auto_plan_requeues_failed_wake(tmp_path: Path, iso: dict) -> None:
    wake = _wake(tmp_path)
    msg = wake.post("old failure", sender="test")
    wake.mark_failed(msg["id"], "ConnectionError: deepseek circuit open: HTTP 402")
    service = _service(iso)
    plan = service.submit("requeue_wake", params={"provider": "deepseek"})
    result = service.execute(plan["plan_id"], operator="test")
    assert result["status"] == STATUS_COMPLETED
    assert result["verification"]["valid"] is True
    assert result["apply"]["requeued"] == 1
    assert wake.get_inbox(msg["id"])["status"] == "pending"
    events = [r.event_type for r in iso["audit"].get_chain(component="repair")]
    assert "repair.executing" in events and "repair.verified" in events and "repair.completed" in events


def test_execute_operator_plan_requires_approval(iso: dict) -> None:
    service = _service(iso)
    plan = service.submit("quarantine_wake", params={})
    with pytest.raises(ValueError, match="requires operator approval"):
        service.execute(plan["plan_id"], operator="test")


def test_execute_operator_plan_after_approval(tmp_path: Path, iso: dict) -> None:
    wake = _wake(tmp_path)
    wake.post("stuck", sender="test")
    wake.post("stuck2", sender="test")
    service = _service(iso)
    plan = service.submit("quarantine_wake", params={})
    approved = service.approve(plan["plan_id"], operator="wilson")
    assert approved["status"] == "approved"
    assert approved["decided_by"] == "wilson"
    result = service.execute(plan["plan_id"], operator="wilson")
    assert result["status"] == STATUS_COMPLETED
    assert result["verification"]["pending"] == 0
    assert wake.pending_count() == 0


def test_execute_verification_failure_rolls_back(tmp_path: Path, iso: dict) -> None:
    """requeue on an empty inbox: apply moves 0, contract unmet → rollback."""
    wake = _wake(tmp_path)
    assert wake.pending_count() == 0
    service = _service(iso)
    plan = service.submit("requeue_wake", params={})
    result = service.execute(plan["plan_id"], operator="test")
    assert result["status"] == STATUS_ROLLED_BACK
    assert result["verification"]["valid"] is False
    events = [r.event_type for r in iso["audit"].get_chain(component="repair")]
    assert "repair.rolled_back" in events


def test_execute_kill_switch_blocks(tmp_path: Path, iso: dict) -> None:
    iso["kill_switch"]._armed = True
    service = _service(iso)
    plan = service.submit("requeue_wake", params={})
    result = service.execute(plan["plan_id"], operator="test")
    assert result["status"] == STATUS_FAILED
    assert "kill switch" in result["error"]


def test_execute_verify_before_trust_blocks(tmp_path: Path, iso: dict) -> None:
    chain = iso["audit"]
    chain.append("test", "test.event", {"n": 1})
    chain.append("test", "test.event", {"n": 2})
    tamper(chain.db_path, "UPDATE audit_records SET payload='{\"n\": 999}' WHERE seq=2")
    service = _service(iso)
    plan = service.submit("requeue_wake", params={})
    result = service.execute(plan["plan_id"], operator="test")
    assert result["status"] == STATUS_FAILED
    assert "chain not trustworthy" in result["error"]


def test_resolve_discrepancy_flow(tmp_path: Path, iso: dict) -> None:
    iso["disc"].insert(
        Discrepancy(
            id="d9", timestamp="2026-08-22T00:00:00+00:00", subsystem="automation_audit",
            expected_state="provider within bounds", observed_state="MSB_ZAPIER_API_KEY not set",
            discrepancy_type="provider_unavailable", severity=SEV_WARN, evidence={},
            confidence=1.0, affected_resource="zapier", suggested_action="resolve",
        )
    )
    service = _service(iso)
    plan = service.submit(
        "resolve_discrepancy", params={"discrepancy_id": "d9"}, discrepancy_id="d9",
        root_cause="zapier provider configured"
    )
    service.approve(plan["plan_id"], operator="wilson")
    result = service.execute(plan["plan_id"], operator="wilson")
    assert result["status"] == STATUS_COMPLETED
    assert result["verification"]["valid"] is True
    rows = iso["disc"].query(status="open")
    assert all(r["id"] != "d9" for r in rows)


def test_store_status_transitions_and_list(iso: dict) -> None:
    service = _service(iso)
    plan = service.submit("requeue_wake", params={})  # AUTO → proposed, ready
    rows = iso["store"].list(status="proposed")
    assert [r["plan_id"] for r in rows] == [plan["plan_id"]]
    assert iso["store"].list(status=STATUS_AWAITING) == []
    assert iso["store"].list(status=STATUS_COMPLETED) == []
