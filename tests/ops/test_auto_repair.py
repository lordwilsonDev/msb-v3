"""Phase 4 — AutoRepairLoop: bounded automatic repair.

Pins the contract: one cycle = scan → diagnose → propose (deduped) → execute
AUTO plans only. requeue_wake executes only after the provider has been quiet
(recovery guard); quarantine_wake is proposed but NEVER executed by the loop;
dedupe keeps one open plan per action+params; the kill switch and a tampered
chain block the cycle; dry-run changes nothing; every cycle is persisted and
audited.
"""

from __future__ import annotations

import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from msb_ledger.audit_chain import AuditChain, tamper
from msb_v3.core.config import settings
from msb_v3.ops.auto_repair import (
    STATUS_COMPLETED,
    STATUS_PROPOSED,
    AutoRepairLoop,
    AutoRepairStore,
)
from msb_v3.ops.discrepancy import DiscrepancyStore
from msb_v3.ops.repair import REPAIR_ACTIONS, RepairService, RepairStore
from msb_v3.ops.root_cause import RootCauseEngine


class _FakeKillSwitch:
    def __init__(self, armed: bool = False) -> None:
        self._armed = armed

    def is_armed(self) -> bool:
        return self._armed


class _StubDiscEngine:
    """DiscrepancyEngine stand-in — detector behavior is Phase 1's contract."""

    def __init__(self) -> None:
        pass

    def scan(self) -> dict:
        return {
            "ok": True,
            "detectors": [],
            "new_discrepancies": 0,
            "already_open": 0,
            "counts": {"total": 0, "by_status": {}, "by_severity": {}, "open_critical": 0},
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hours_ago(h: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=h)).isoformat()


def _minutes_ago(m: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=m)).isoformat()


@pytest.fixture()
def iso(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """All stores pinned to tmp — never touches production data. settings.db_path
    is redirected so the loop's lock, wake store, and anchor resolve to scratch
    files; wake_db_path is cleared so it derives from the pinned db_path."""
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "data" / "msb.db"))
    monkeypatch.setattr(settings, "wake_db_path", "")
    # The real reanchor apply runs scripts/notarize_chain_anchor.sh against the
    # PRODUCTION chain — it must never execute from a test. Stub it so the
    # anchor file simply becomes fresh (which satisfies the verification
    # contract), matching what the real script does.
    anchor_path = Path(settings.db_path).parent / "uac" / "chain_anchor.json"

    def _fake_reanchor_apply(params: dict, store=None) -> dict:
        anchor_path.parent.mkdir(parents=True, exist_ok=True)
        anchor_path.touch()
        return {"ran": False}

    monkeypatch.setitem(REPAIR_ACTIONS["reanchor_chain"], "apply", _fake_reanchor_apply)
    # Baseline: a fresh anchor — a missing/stale anchor is itself a repair
    # trigger (re-anchor stale chain), so only the staleness test makes it old.
    anchor_path.parent.mkdir(parents=True, exist_ok=True)
    anchor_path.touch()
    stores = {
        "repairs": RepairStore(db_path=str(tmp_path / "repairs.db")),
        "audit": AuditChain(db_path=str(tmp_path / "audit.db"), allow_keyless=True),
        "kill": _FakeKillSwitch(),
        "disc": DiscrepancyStore(db_path=str(tmp_path / "disc.db")),
        "cycles": AutoRepairStore(db_path=str(tmp_path / "repairs.db")),
        "wake_db": str(tmp_path / "data" / "runtime" / "wake.db"),
        "cron_db": str(tmp_path / "cron.db"),
    }
    return stores


def _seed_wake(wake_db: str, rows: list) -> None:
    """rows: (id, ts, status, error)"""
    Path(wake_db).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(wake_db)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS wake_inbox (
            id TEXT PRIMARY KEY,
            ts TEXT NOT NULL,
            sender TEXT NOT NULL,
            text TEXT NOT NULL,
            status TEXT NOT NULL,
            response_id TEXT,
            error TEXT,
            responded_at TEXT
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


def _wake_counts(wake_db: str) -> dict:
    conn = sqlite3.connect(wake_db)
    counts = {
        s: conn.execute("SELECT COUNT(*) FROM wake_inbox WHERE status=?", (s,)).fetchone()[0]
        for s in ("pending", "failed")
    }
    conn.close()
    return counts


def _make_loop(iso: dict) -> AutoRepairLoop:
    from msb_v3.ops.verify import VerificationStore, VerifyEngine

    rc = RootCauseEngine(
        wake_db=iso["wake_db"],
        cron_db=iso["cron_db"],
        discrepancy_store=iso["disc"],
        chain=iso["audit"],
    )
    service = RepairService(
        store=iso["repairs"],
        audit=iso["audit"],
        kill_switch=iso["kill"],
        discrepancy_store=iso["disc"],
    )
    verify = VerifyEngine(
        store=VerificationStore(db_path=str(Path(iso["repairs"].db_path))),
        repair_service=service,
        discrepancy_engine=_StubDiscEngine(),
        discrepancy_store=iso["disc"],
        chain=iso["audit"],
        wake_db=iso["wake_db"],
    )
    return AutoRepairLoop(
        service=service,
        store=iso["cycles"],
        discrepancy_engine=_StubDiscEngine(),
        root_cause_engine=rc,
        discrepancy_store=iso["disc"],
        chain=iso["audit"],
        kill_switch=iso["kill"],
        verify_engine=verify,
    )


# --- healthy / audit -------------------------------------------------------


def test_healthy_system_no_op_cycle_audited(iso: dict) -> None:
    # fresh anchor + empty stores → nothing to propose, nothing to execute
    anchor = Path(settings.db_path).parent / "uac" / "chain_anchor.json"
    anchor.parent.mkdir(parents=True, exist_ok=True)
    anchor.write_text("fresh")
    loop = _make_loop(iso)
    report = loop.run()
    assert report["status"] == "completed"
    assert report["proposed"] == []
    assert report["executed"] == []
    assert report["deferred"] == []
    assert report["chain"]["valid"] is True

    # cycle persisted
    last = iso["cycles"].last_cycle()
    assert last is not None and last["status"] == "completed"

    # cycle mirrored to the anchored chain
    records = iso["audit"].get_chain(component="auto_repair")
    assert len(records) >= 1
    assert any(getattr(r, "event_type", "") == "auto_repair.cycle" for r in records)


# --- requeue_wake (AUTO) ---------------------------------------------------


def test_provider_outage_requeue_auto_executes(iso: dict) -> None:
    _seed_wake(
        iso["wake_db"],
        [
            ("w1", _hours_ago(2), "failed", "ConnectionError: deepseek circuit open: HTTP 402 (payment required)"),
            ("w2", _hours_ago(2), "failed", "ConnectionError: deepseek circuit open: HTTP 402 (payment required)"),
            ("w3", _hours_ago(2), "failed", "ConnectionError: deepseek circuit open: HTTP 402 (payment required)"),
        ],
    )
    report = _make_loop(iso).run()

    assert any(p["action"] == "requeue_wake" for p in report["proposed"])
    executed = [e for e in report["executed"] if e["action"] == "requeue_wake"]
    assert len(executed) == 1
    assert executed[0]["apply"] == {"requeued": 3}

    # messages are pending again — the repair took effect
    counts = _wake_counts(iso["wake_db"])
    assert counts["pending"] == 3
    assert counts["failed"] == 0

    plans = iso["repairs"].list()
    requeue = [p for p in plans if p["action"] == "requeue_wake"]
    assert len(requeue) == 1
    assert requeue[0]["status"] == STATUS_COMPLETED

    # closed loop (Phase 5): the executed repair was verified — target
    # resolved (failed → 0), no new discrepancies
    assert len(report["verifications"]) == 1
    assert report["verifications"][0]["plan_id"] == requeue[0]["plan_id"]
    assert report["verifications"][0]["verdict"] == "verified"


def test_fresh_failures_defer_requeue(iso: dict) -> None:
    # Provider still failing (2 min ago) → requeue must NOT execute yet.
    _seed_wake(
        iso["wake_db"],
        [("w1", _minutes_ago(2), "failed", "ConnectionError: deepseek circuit open: HTTP 402 (payment required)")],
    )
    report = _make_loop(iso).run()

    assert any(p["action"] == "requeue_wake" for p in report["proposed"])
    assert report["executed"] == []
    assert any("still failing" in d["reason"] for d in report["deferred"])

    plan = [p for p in iso["repairs"].list() if p["action"] == "requeue_wake"][0]
    assert plan["status"] == STATUS_PROPOSED  # retried on a later cycle


def test_dedupe_one_open_plan_per_provider(iso: dict) -> None:
    _seed_wake(
        iso["wake_db"],
        [("w1", _minutes_ago(2), "failed", "ConnectionError: deepseek circuit open: HTTP 402 (payment required)")],
    )
    loop = _make_loop(iso)
    loop.run()
    second = loop.run()

    assert second["deduped"] == 1
    requeue = [p for p in iso["repairs"].list() if p["action"] == "requeue_wake"]
    assert len(requeue) == 1  # one open plan, never a plan storm


def test_quarantine_dedupes_across_changing_backlog(iso: dict) -> None:
    # The quarantine note param carries a live backlog count that changes
    # between cycles — the dedupe identity is the action, not the note.
    _seed_wake(
        iso["wake_db"],
        [
            ("w1", _hours_ago(2), "failed", "ConnectionError: deepseek circuit open: HTTP 402 (payment required)"),
            ("p1", _hours_ago(1), "pending", None),
            ("p2", _hours_ago(1), "pending", None),
        ],
    )
    loop = _make_loop(iso)
    first = loop.run()
    assert any(p["action"] == "quarantine_wake" for p in first["proposed"])

    # backlog grows → the note changes, but the plan must not duplicate
    _seed_wake(iso["wake_db"], [("p3", _minutes_ago(1), "pending", None)])
    second = loop.run()
    assert second["deduped"] >= 1
    quarantines = [p for p in iso["repairs"].list() if p["action"] == "quarantine_wake"]
    assert len(quarantines) == 1


def test_max_execute_cap(iso: dict) -> None:
    _seed_wake(
        iso["wake_db"],
        [
            ("w1", _hours_ago(2), "failed", "ConnectionError: deepseek circuit open: HTTP 402 (payment required)"),
            ("w2", _hours_ago(2), "failed", "ConnectionError: ollama timed out"),
        ],
    )
    report = _make_loop(iso).run(max_auto_execute=1)

    assert len(report["executed"]) == 1
    assert any("cap 1 reached" in d["reason"] for d in report["deferred"])
    # the deferred plan stays open for the next cycle
    open_auto = [p for p in iso["repairs"].list() if p["required_authority"] == "AUTO" and p["status"] == STATUS_PROPOSED]
    assert len(open_auto) == 1


# --- quarantine_wake (OPERATOR) — proposed, never executed ------------------


def test_quarantine_proposed_but_never_auto_executed(iso: dict) -> None:
    _seed_wake(
        iso["wake_db"],
        [
            ("w1", _hours_ago(2), "failed", "ConnectionError: deepseek circuit open: HTTP 402 (payment required)"),
            ("p1", _hours_ago(1), "pending", None),
            ("p2", _hours_ago(1), "pending", None),
        ],
    )
    report = _make_loop(iso).run()

    # backlog → quarantine proposed (OPERATOR)
    quarantines = [p for p in report["proposed"] if p["action"] == "quarantine_wake"]
    assert len(quarantines) == 1
    plan = [p for p in iso["repairs"].list() if p["action"] == "quarantine_wake"][0]
    assert plan["status"] == "awaiting_approval"

    # the loop never executes it — only the AUTO requeue acts (w1 → pending)
    assert all(e["action"] != "quarantine_wake" for e in report["executed"])
    counts = _wake_counts(iso["wake_db"])
    assert counts["pending"] == 3  # p1, p2 + requeued w1
    assert counts["failed"] == 0


# --- governance brakes ------------------------------------------------------


def test_kill_switch_blocks_cycle(iso: dict) -> None:
    iso["kill"]._armed = True
    _seed_wake(
        iso["wake_db"],
        [("w1", _hours_ago(2), "failed", "ConnectionError: deepseek circuit open: HTTP 402 (payment required)")],
    )
    report = _make_loop(iso).run()
    assert report["status"] == "kill_switch_armed"
    assert iso["repairs"].list() == []  # nothing proposed, nothing executed


def test_tampered_chain_blocks_execution_and_reanchor(iso: dict) -> None:
    iso["audit"].append("test", "seed.one", {})
    iso["audit"].append("test", "seed.two", {})
    tamper(iso["audit"].db_path, "UPDATE audit_records SET payload='{\"n\": 999}' WHERE seq=2")

    anchor = Path(settings.db_path).parent / "uac" / "chain_anchor.json"
    anchor.parent.mkdir(parents=True, exist_ok=True)
    anchor.write_text("stale anchor")
    old = time.time() - 3600
    os.utime(anchor, (old, old))

    _seed_wake(
        iso["wake_db"],
        [("w1", _hours_ago(2), "failed", "ConnectionError: deepseek circuit open: HTTP 402 (payment required)")],
    )
    report = _make_loop(iso).run()

    assert report["chain"]["valid"] is False
    # reanchor is never proposed over a broken chain (tamper evidence preserved)
    assert all(p["action"] != "reanchor_chain" for p in report["proposed"])
    # requeue proposed but execution refused by verify-before-trust
    assert any(p["action"] == "requeue_wake" for p in report["proposed"])
    assert report["executed"] == []
    assert len(report["failed"]) >= 1
    assert "not trustworthy" in report["failed"][0]["error"]
    plan = [p for p in iso["repairs"].list() if p["action"] == "requeue_wake"][0]
    assert plan["status"] == "failed"


def test_stale_anchor_proposes_and_executes_reanchor(iso: dict) -> None:
    # reanchor apply is stubbed by the iso fixture (never the real script)
    anchor = Path(settings.db_path).parent / "uac" / "chain_anchor.json"
    anchor.parent.mkdir(parents=True, exist_ok=True)
    anchor.write_text("stale anchor")
    old = time.time() - 3600
    os.utime(anchor, (old, old))

    report = _make_loop(iso).run()

    assert any(p["action"] == "reanchor_chain" for p in report["proposed"])
    assert any(e["action"] == "reanchor_chain" for e in report["executed"])
    plans = [p for p in iso["repairs"].list() if p["action"] == "reanchor_chain"]
    assert len(plans) == 1
    assert plans[0]["status"] == STATUS_COMPLETED


# --- dry-run / disabled -----------------------------------------------------


def test_dry_run_changes_nothing(iso: dict) -> None:
    _seed_wake(
        iso["wake_db"],
        [("w1", _hours_ago(2), "failed", "ConnectionError: deepseek circuit open: HTTP 402 (payment required)")],
    )
    report = _make_loop(iso).run(dry_run=True)

    assert report["status"] == "dry_run"
    assert any(p["action"] == "requeue_wake" for p in report["would_propose"])
    assert iso["repairs"].list() == []  # nothing proposed
    assert _wake_counts(iso["wake_db"])["failed"] == 1  # nothing executed


def test_disabled_flag_blocks_cycle(iso: dict, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "auto_repair_enabled", False)
    _seed_wake(
        iso["wake_db"],
        [("w1", _hours_ago(2), "failed", "ConnectionError: deepseek circuit open: HTTP 402 (payment required)")],
    )
    report = _make_loop(iso).run()
    assert report["status"] == "disabled"
    assert iso["repairs"].list() == []


def test_status_surface(iso: dict) -> None:
    loop = _make_loop(iso)
    loop.run()
    status = loop.status()
    assert status["ok"] is True
    assert status["last_cycle"]["status"] == "completed"
    assert "auto-repair" in status["schedule"]
