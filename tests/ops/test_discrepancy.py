"""Phase 1 — DiscrepancyEngine: one normalized diagnostic layer.

Pins the contract: raw findings from every detector normalize into the single
``Discrepancy`` object, dedupe on fingerprint (open discrepancies bump
last_seen, never re-insert), new discrepancies mirror to the audit chain, and
a detector that raises is a finding — never a crash. Detector adapters are
tested against real stores (tampered chain -> chain_invalid, drifted
projection -> projection_divergence).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from msb_ledger.audit_chain import AuditChain, tamper
from msb_v3.core.config import settings
from msb_v3.ops.discrepancy import (
    SEV_CRITICAL,
    SEV_WARN,
    STATUS_OPEN,
    Discrepancy,
    DiscrepancyEngine,
    DiscrepancyStore,
    _detect_audit_chain,
    _detect_replay,
)
from msb_v3.tasks.lifecycle import TaskLifecycle
from msb_v3.tasks.models import UnifiedTask


@pytest.fixture()
def iso_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point settings.db_path at tmp so the audit chain + task store detectors
    resolve to hermetic scratch files (chain at <tmp>/data/uac/, tasks at
    <tmp>/data/runtime/)."""
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "data" / "msb.db"))
    return tmp_path


@pytest.fixture()
def store(tmp_path: Path) -> DiscrepancyStore:
    return DiscrepancyStore(db_path=str(tmp_path / "discrepancies.db"))


def _finding(**overrides) -> dict:
    base = {
        "subsystem": "test",
        "expected_state": "expected",
        "observed_state": "observed",
        "discrepancy_type": "test_mismatch",
        "severity": SEV_WARN,
        "evidence": {"k": "v"},
        "confidence": 1.0,
        "affected_resource": "res.1",
        "suggested_action": "inspect",
    }
    base.update(overrides)
    return base


# --- store ----------------------------------------------------------------


def test_store_insert_query_counts(store: DiscrepancyStore) -> None:
    d = Discrepancy(
        id="d1",
        timestamp="2026-08-22T00:00:00+00:00",
        subsystem="replay",
        expected_state="COMPLETED",
        observed_state="PLANNED",
        discrepancy_type="projection_divergence",
        severity=SEV_CRITICAL,
        evidence={"task_id": "t.1"},
        confidence=1.0,
        affected_resource="t.1",
        suggested_action="quarantine",
    )
    store.insert(d)
    rows = store.query(subsystem="replay")
    assert len(rows) == 1
    assert rows[0]["evidence"]["task_id"] == "t.1"
    assert store.query(severity=SEV_WARN) == []
    counts = store.counts()
    assert counts["total"] == 1
    assert counts["by_severity"][SEV_CRITICAL] == 1
    assert counts["open_critical"] == 1


def test_store_query_filters(store: DiscrepancyStore) -> None:
    store.insert(
        Discrepancy(
            id="a", timestamp="2026-08-22T00:00:00+00:00", subsystem="replay",
            expected_state="x", observed_state="y", discrepancy_type="t1",
            severity=SEV_CRITICAL, evidence={}, confidence=1.0,
            affected_resource="r", suggested_action="",
        )
    )
    store.insert(
        Discrepancy(
            id="b", timestamp="2026-08-22T00:00:00+00:00", subsystem="automation_audit",
            expected_state="x", observed_state="y", discrepancy_type="t2",
            severity=SEV_WARN, evidence={}, confidence=1.0,
            affected_resource="r2", suggested_action="",
        )
    )
    assert len(store.query(subsystem="replay")) == 1
    assert len(store.query(severity=SEV_WARN)) == 1
    assert len(store.query(subsystem="replay", status=STATUS_OPEN)) == 1
    assert store.query(subsystem="nope") == []


# --- engine: record / dedupe / audit --------------------------------------


class _FakeAudit:
    def __init__(self) -> None:
        self.appends: list[tuple] = []

    def append(self, component: str, event_type: str, payload: dict) -> None:
        self.appends.append((component, event_type, payload))


def test_record_persists_and_mirrors_to_audit(store: DiscrepancyStore) -> None:
    audit = _FakeAudit()
    engine = DiscrepancyEngine(store=store, audit=audit)
    d = engine.record(_finding())
    assert d is not None
    assert d.subsystem == "test"
    assert audit.appends == [
        ("discrepancy_engine", "discrepancy.recorded", {
            "discrepancy_id": d.id, "subsystem": "test", "type": "test_mismatch",
            "severity": SEV_WARN, "resource": "res.1",
        })
    ]
    assert len(store.query()) == 1


def test_record_dedupes_open_fingerprint(store: DiscrepancyStore) -> None:
    engine = DiscrepancyEngine(store=store, audit=_FakeAudit())
    first = engine.record(_finding())
    second = engine.record(_finding())  # same subsystem/type/resource
    assert first is not None
    assert second is None  # deduped
    rows = store.query()
    assert len(rows) == 1  # never re-inserted
    assert rows[0]["last_seen"] >= rows[0]["timestamp"]


def test_record_distinct_resources_both_persist(store: DiscrepancyStore) -> None:
    engine = DiscrepancyEngine(store=store, audit=_FakeAudit())
    engine.record(_finding(affected_resource="res.1"))
    engine.record(_finding(affected_resource="res.2"))
    assert len(store.query()) == 2


# --- engine: scan ---------------------------------------------------------


def test_scan_records_findings_and_reports(store: DiscrepancyStore, monkeypatch) -> None:
    from msb_v3.ops import discrepancy as disc

    monkeypatch.setattr(disc, "_detect_replay", lambda: [_finding(subsystem="replay")])
    engine = DiscrepancyEngine(store=store, audit=_FakeAudit(), detectors=["replay"])
    report = engine.scan()
    assert report["ok"] is True
    assert report["new_discrepancies"] == 1
    assert report["detectors"] == [{"detector": "replay", "findings": 1}]
    assert len(store.query(subsystem="replay")) == 1


def test_scan_detector_error_is_a_finding_not_a_crash(store: DiscrepancyStore, monkeypatch) -> None:
    from msb_v3.ops import discrepancy as disc

    def _boom() -> list:
        raise RuntimeError("detector exploded")

    monkeypatch.setattr(disc, "_detect_replay", _boom)
    engine = DiscrepancyEngine(store=store, audit=_FakeAudit(), detectors=["replay"])
    report = engine.scan()
    assert report["ok"] is True
    rows = store.query(subsystem="replay")
    assert len(rows) == 1
    assert rows[0]["discrepancy_type"] == "detector_error"
    assert "exploded" in rows[0]["observed_state"]


def test_scan_dedupes_across_scans(store: DiscrepancyStore, monkeypatch) -> None:
    from msb_v3.ops import discrepancy as disc

    monkeypatch.setattr(disc, "_detect_replay", lambda: [_finding(subsystem="replay")])
    engine = DiscrepancyEngine(store=store, audit=_FakeAudit(), detectors=["replay"])
    first = engine.scan()
    second = engine.scan()
    assert first["new_discrepancies"] == 1
    assert second["new_discrepancies"] == 0
    assert second["already_open"] == 1
    assert len(store.query()) == 1


# --- detector adapters (real stores) --------------------------------------


def _make_chain(chain_db: Path) -> AuditChain:
    chain = AuditChain(db_path=str(chain_db), allow_keyless=True)
    chain.append("test", "test.event", {"n": 1})
    chain.append("test", "test.event", {"n": 2})
    return chain


def test_detect_audit_chain_clean(iso_settings: Path) -> None:
    chain_db = iso_settings / "data" / "uac" / "audit_chain.db"
    chain_db.parent.mkdir(parents=True, exist_ok=True)
    _make_chain(chain_db)
    assert _detect_audit_chain() == []


def test_detect_audit_chain_tampered(iso_settings: Path) -> None:
    chain_db = iso_settings / "data" / "uac" / "audit_chain.db"
    chain_db.parent.mkdir(parents=True, exist_ok=True)
    _make_chain(chain_db)
    # Corrupt a payload in place — the hash chain must catch it.
    tamper(chain_db, "UPDATE audit_records SET payload='{\"n\": 999}' WHERE seq=2")
    findings = _detect_audit_chain()
    assert len(findings) == 1
    assert findings[0]["discrepancy_type"] == "chain_invalid"
    assert findings[0]["severity"] == SEV_CRITICAL
    assert findings[0]["confidence"] == 1.0


def test_detect_replay_divergence(iso_settings: Path) -> None:
    lifecycle = TaskLifecycle(db_path=str(iso_settings / "data" / "runtime" / "tasks.db"))
    task = UnifiedTask(task_id="t.div", kind="agent.run", tenant="wilson-vault", session="s")
    lifecycle.create(task)
    for state in ("PLANNED", "EXECUTING", "VERIFYING", "COMPLETED"):
        lifecycle.transition("t.div", state)
    # Drift the stored projection while leaving the event log intact.
    with sqlite3.connect(str(iso_settings / "data" / "runtime" / "tasks.db")) as conn:
        conn.execute("UPDATE unified_tasks SET state='PLANNED' WHERE task_id='t.div'")
    findings = _detect_replay()
    assert len(findings) == 1
    assert findings[0]["discrepancy_type"] == "projection_divergence"
    assert findings[0]["affected_resource"] == "t.div"
    assert findings[0]["severity"] == SEV_CRITICAL


def test_detect_replay_clean(iso_settings: Path) -> None:
    lifecycle = TaskLifecycle(db_path=str(iso_settings / "data" / "runtime" / "tasks.db"))
    task = UnifiedTask(task_id="t.ok", kind="agent.run", tenant="wilson-vault", session="s")
    lifecycle.create(task)
    for state in ("PLANNED", "EXECUTING", "VERIFYING", "COMPLETED"):
        lifecycle.transition("t.ok", state)
    assert _detect_replay() == []


def test_detect_replay_illegal_transition(iso_settings: Path) -> None:
    tasks_db = str(iso_settings / "data" / "runtime" / "tasks.db")
    lifecycle = TaskLifecycle(db_path=tasks_db)
    task = UnifiedTask(task_id="t.illegal", kind="agent.run", tenant="wilson-vault", session="s")
    lifecycle.create(task)
    for state in ("PLANNED", "EXECUTING", "VERIFYING", "COMPLETED"):
        lifecycle.transition("t.illegal", state)
    # Rewrite one event's state to an illegal jump (EXECUTING -> DENIED is not
    # a legal transition) — the replay engine must flag the sequence.
    with sqlite3.connect(tasks_db) as conn:
        conn.execute(
            "UPDATE task_events SET state='DENIED' WHERE task_id='t.illegal' AND state='EXECUTING'"
        )
    findings = _detect_replay()
    assert len(findings) == 1
    assert findings[0]["discrepancy_type"] == "illegal_transition"
