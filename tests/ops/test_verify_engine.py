"""Phase 5 — VerifyEngine: closed-loop verification.

Pins the contract: every executed repair gets a deterministic verdict —
verified (target resolved, no new discrepancies), not_verified (target still
present), regressed (new discrepancy appeared — the roadmap's second
question), inconclusive (evidence missing — never overclaimed). The before
snapshot is captured by the caller; the after snapshot includes a fresh
scan; verdicts persist append-only and mirror to the audit chain.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from msb_ledger.audit_chain import AuditChain
from msb_v3.core.config import settings
from msb_v3.ops.discrepancy import Discrepancy, DiscrepancyStore
from msb_v3.ops.repair import RepairService, RepairStore
from msb_v3.ops.verify import (
    VERDICT_INCONCLUSIVE,
    VERDICT_NOT_VERIFIED,
    VERDICT_REGRESSED,
    VERDICT_VERIFIED,
    VerificationStore,
    VerifyEngine,
    compute_verdict,
)


class _FakeKillSwitch:
    def __init__(self) -> None:
        pass

    def is_armed(self) -> bool:
        return False


class _StubDiscEngine:
    def scan(self) -> dict:
        return {"ok": True, "detectors": [], "new_discrepancies": 0, "already_open": 0, "counts": {}}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hours_ago(h: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=h)).isoformat()


def _snap(*, failed=None, pending=None, fps=None, ids=None, anchor=None) -> dict:
    s: dict = {"ts": _now_iso()}
    s["open_fingerprints"] = fps
    s["open_discrepancy_ids"] = ids
    s["wake_failed_by_provider"] = failed
    s["wake_pending"] = pending
    s["anchor_age_s"] = anchor
    return s


@pytest.fixture()
def iso(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "data" / "msb.db"))
    monkeypatch.setattr(settings, "wake_db_path", "")
    anchor = Path(settings.db_path).parent / "uac" / "chain_anchor.json"
    anchor.parent.mkdir(parents=True, exist_ok=True)
    anchor.touch()
    stores = {
        "repairs": RepairStore(db_path=str(tmp_path / "repairs.db")),
        "audit": AuditChain(db_path=str(tmp_path / "audit.db"), allow_keyless=True),
        "disc": DiscrepancyStore(db_path=str(tmp_path / "disc.db")),
        "verify": VerificationStore(db_path=str(tmp_path / "repairs.db")),
        "wake_db": str(tmp_path / "data" / "runtime" / "wake.db"),
    }
    return stores


def _seed_wake(wake_db: str, rows: list) -> None:
    Path(wake_db).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(wake_db)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS wake_inbox (
            id TEXT PRIMARY KEY, ts TEXT NOT NULL, sender TEXT NOT NULL,
            text TEXT NOT NULL, status TEXT NOT NULL, response_id TEXT,
            error TEXT, responded_at TEXT
        )
        """
    )
    for rid, ts, status, error in rows:
        conn.execute(
            "INSERT INTO wake_inbox (id, ts, sender, text, status, response_id, error, responded_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (rid, ts, "tester", "hello", status, None, error, None),
        )
    conn.commit()
    conn.close()


def _engine(iso: dict) -> tuple:
    service = RepairService(
        store=iso["repairs"],
        audit=iso["audit"],
        kill_switch=_FakeKillSwitch(),
        discrepancy_store=iso["disc"],
    )
    engine = VerifyEngine(
        store=iso["verify"],
        repair_service=service,
        discrepancy_engine=_StubDiscEngine(),
        discrepancy_store=iso["disc"],
        chain=iso["audit"],
        wake_db=iso["wake_db"],
    )
    return engine, service


# --- pure verdict matrix ---------------------------------------------------


def test_requeue_verified() -> None:
    before = _snap(failed={"deepseek": 3}, pending=2, fps=["a:b:c"], ids=["d1"])
    after = _snap(failed={"deepseek": 0}, pending=5, fps=["a:b:c"], ids=["d1"])
    v = compute_verdict("requeue_wake", {"provider": "deepseek"}, before, after)
    assert v["verdict"] == VERDICT_VERIFIED
    assert v["forward_resolved"] is True
    assert v["new_discrepancies"] == []
    assert v["regression_assessed"] is True


def test_requeue_not_verified() -> None:
    before = _snap(failed={"deepseek": 3}, fps=["a:b:c"], ids=["d1"])
    after = _snap(failed={"deepseek": 3}, fps=["a:b:c"], ids=["d1"])
    v = compute_verdict("requeue_wake", {"provider": "deepseek"}, before, after)
    assert v["verdict"] == VERDICT_NOT_VERIFIED
    assert v["forward_resolved"] is False


def test_requeue_regressed() -> None:
    before = _snap(failed={"deepseek": 3}, fps=["a:b:c"], ids=["d1"])
    after = _snap(failed={"deepseek": 0}, fps=["a:b:c", "new:type:res"], ids=["d1", "d2"])
    v = compute_verdict("requeue_wake", {"provider": "deepseek"}, before, after)
    assert v["verdict"] == VERDICT_REGRESSED
    assert v["forward_resolved"] is True
    assert v["new_discrepancies"] == ["new:type:res"]


def test_no_before_snapshot_inconclusive() -> None:
    after = _snap(failed={"deepseek": 0}, fps=["a:b:c"], ids=["d1"])
    v = compute_verdict("requeue_wake", {"provider": "deepseek"}, None, after)
    assert v["verdict"] == VERDICT_INCONCLUSIVE
    assert v["forward_resolved"] is True  # the forward answer is still given
    assert "no before snapshot" in v["detail"]
    assert v["regression_assessed"] is False


def test_missing_evidence_inconclusive() -> None:
    before = _snap(failed={"deepseek": 3}, fps=["a:b:c"], ids=["d1"])
    after = _snap(failed=None, fps=["a:b:c"], ids=["d1"])
    v = compute_verdict("requeue_wake", {"provider": "deepseek"}, before, after)
    assert v["verdict"] == VERDICT_INCONCLUSIVE
    assert v["forward_resolved"] is None


def test_quarantine_forward() -> None:
    before = _snap(pending=3, fps=["a:b:c"], ids=["d1"])
    after = _snap(pending=0, fps=["a:b:c"], ids=["d1"])
    v = compute_verdict("quarantine_wake", {"note": "x"}, before, after)
    assert v["verdict"] == VERDICT_VERIFIED
    assert v["forward_resolved"] is True


def test_reanchor_forward() -> None:
    before = _snap(anchor=3600.0, fps=[], ids=[])
    after = _snap(anchor=1.0, fps=[], ids=[])
    v = compute_verdict("reanchor_chain", {}, before, after)
    assert v["verdict"] == VERDICT_VERIFIED


def test_resolve_discrepancy_forward() -> None:
    before = _snap(ids=["d1", "d2"], fps=["a:b:c", "a:b:d"])
    after = _snap(ids=["d2"], fps=["a:b:d"])
    v = compute_verdict("resolve_discrepancy", {"discrepancy_id": "d1"}, before, after)
    assert v["verdict"] == VERDICT_VERIFIED


# --- engine integration ----------------------------------------------------


def test_execute_then_verify_requeue(iso: dict) -> None:
    _seed_wake(
        iso["wake_db"],
        [("w1", _hours_ago(2), "failed", "ConnectionError: deepseek circuit open: HTTP 402 (payment required)")],
    )
    engine, service = _engine(iso)
    plan = service.submit("requeue_wake", params={"provider": "deepseek"}, root_cause="deepseek outage")

    before = engine.capture()
    result = service.execute(plan["plan_id"], operator="tester")
    assert result["status"] == "completed"

    report = engine.verify_repair(plan["plan_id"], before=before)
    assert report["verdict"] == VERDICT_VERIFIED
    assert report["forward_resolved"] is True
    assert "3" not in report["forward_detail"]  # single message: 1 → 0
    assert report["new_discrepancies"] == []

    # persisted (append-only) + audited
    row = iso["verify"].latest(plan["plan_id"])
    assert row is not None and row["verdict"] == VERDICT_VERIFIED
    events = iso["audit"].get_chain(component="repair")
    assert any(getattr(e, "event_type", "") == "repair.verification" for e in events)


def test_verify_refuses_unexecuted_plan(iso: dict) -> None:
    engine, service = _engine(iso)
    plan = service.submit("requeue_wake", params={"provider": "deepseek"}, root_cause="outage")
    with pytest.raises(ValueError):
        engine.verify_repair(plan["plan_id"], before=engine.capture())


def test_verify_detects_regression(iso: dict) -> None:
    _seed_wake(
        iso["wake_db"],
        [("w1", _hours_ago(2), "failed", "ConnectionError: deepseek circuit open: HTTP 402 (payment required)")],
    )
    engine, service = _engine(iso)
    plan = service.submit("requeue_wake", params={"provider": "deepseek"}, root_cause="outage")

    before = engine.capture()
    service.execute(plan["plan_id"], operator="tester")

    # a new discrepancy appears right after the repair (whatever the cause)
    iso["disc"].insert(
        Discrepancy(
            id="new1",
            timestamp=_now_iso(),
            subsystem="test",
            expected_state="expected",
            observed_state="observed",
            discrepancy_type="new_type",
            severity="warn",
            evidence={},
            confidence=1.0,
            affected_resource="res",
            suggested_action="inspect",
        )
    )
    report = engine.verify_repair(plan["plan_id"], before=before)
    assert report["verdict"] == VERDICT_REGRESSED
    assert report["new_discrepancies"] == ["test:new_type:res"]
