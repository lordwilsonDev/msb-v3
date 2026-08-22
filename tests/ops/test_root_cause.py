"""Phase 2 — RootCauseEngine: correlate discrepancies into causal graphs.

Pins the contract: failure strings attribute to providers/kinds, telemetry
collects into normalized signals (wake, cron, discrepancy, boot), correlation
produces evidence-cited causal edges, and diagnosis ranks root causes —
including the canonical incident (DeepSeek 402 → circuit open → wake/cron
failures → backlog → restart).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from msb_ledger.audit_chain import AuditChain
from msb_v3.cron.store import CronStore
from msb_v3.ops.discrepancy import Discrepancy, DiscrepancyStore
from msb_v3.ops.root_cause import RootCauseEngine, parse_error
from msb_v3.wake.store import WakeStore

CIRCUIT_ERR = "ConnectionError: deepseek circuit open: HTTP 402 (payment required) (cooldown 300.0s)"


# --- failure-string attribution -------------------------------------------


def test_parse_error_real_circuit_string() -> None:
    """The exact error the Phase 0 circuit breaker emits in production."""
    attr = parse_error(CIRCUIT_ERR)
    assert attr == {"provider": "deepseek", "kind": "circuit_open", "code": 402}


def test_parse_error_variants() -> None:
    assert parse_error("timed out after 60.0 seconds")["kind"] == "timeout"
    assert parse_error("HTTP 429 Too Many Requests")["code"] == 429
    assert parse_error("httpx.ConnectError: connection refused")["kind"] == "connection"
    assert parse_error("exit 137 (OOM kill)")["kind"] == "oom"
    assert parse_error("OSError: [Errno 28] No space left on device")["kind"] == "disk"
    assert parse_error("unknown failure")["provider"] is None
    assert parse_error("MSB_ZAPIER_API_KEY not set")["provider"] == "zapier"


# --- fixtures --------------------------------------------------------------


@pytest.fixture()
def wake(tmp_path: Path) -> WakeStore:
    return WakeStore(db_path=str(tmp_path / "wake.db"))


@pytest.fixture()
def cron(tmp_path: Path) -> CronStore:
    store = CronStore(db_path=str(tmp_path / "cron.db"))
    store.create_job("local-demo", "local demo", "* * * * *", {"type": "demo"}, enabled=True)
    return store


@pytest.fixture()
def iso_engine(tmp_path: Path) -> dict:
    """Every store pinned to tmp — never touches production wake/cron/
    discrepancy DBs or the live anchored chain."""
    return {
        "wake_db": str(tmp_path / "wake.db"),
        "cron_db": str(tmp_path / "cron.db"),
        "disc": DiscrepancyStore(db_path=str(tmp_path / "disc.db")),
        "chain": AuditChain(db_path=str(tmp_path / "audit.db"), allow_keyless=True),
    }


def _engine(iso_engine: dict, **overrides) -> RootCauseEngine:
    kwargs = {k: v for k, v in iso_engine.items() if k != "disc"}
    kwargs["discrepancy_store"] = iso_engine["disc"]
    kwargs.update(overrides)
    return RootCauseEngine(**kwargs)


def _set_ts(db_path: str, table: str, id_col: str, row_id: str, ts: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(f"UPDATE {table} SET ts=? WHERE {id_col}=?", (ts, row_id))


def _hours_ago(h: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=h)).isoformat()


def _seed_wake_failures(wake: WakeStore, n: int = 3, error: str = CIRCUIT_ERR) -> None:
    for i in range(n):
        msg = wake.post(f"test {i}", sender="test")
        wake.mark_failed(msg["id"], error)


# --- collection ------------------------------------------------------------


def test_collect_wake_aggregates_provider_failure(wake: WakeStore, iso_engine: dict) -> None:
    _seed_wake_failures(wake, n=3)
    wake.post("pending one", sender="test")  # stays pending
    engine = _engine(iso_engine, wake_db=str(wake.db_path))
    signals = engine.collect()
    provider = [s for s in signals if s.kind == "provider_failure"]
    backlog = [s for s in signals if s.kind == "queue_backlog"]
    assert len(provider) == 1
    assert provider[0].resource == "deepseek"
    assert provider[0].meta["count"] == 3
    assert provider[0].meta["kinds"] == ["circuit_open"]
    assert len(backlog) == 1
    assert backlog[0].meta["pending"] == 1


def test_collect_wake_respects_window(wake: WakeStore, iso_engine: dict) -> None:
    msg = wake.post("old failure", sender="test")
    wake.mark_failed(msg["id"], CIRCUIT_ERR)
    _set_ts(str(wake.db_path), "wake_inbox", "id", msg["id"], _hours_ago(48))
    engine = _engine(iso_engine, wake_db=str(wake.db_path), window_hours=24)
    signals = engine.collect()
    assert all(s.source != "wake" or s.kind != "provider_failure" for s in signals)


def test_collect_cron_failures(cron: CronStore, iso_engine: dict) -> None:
    run_id = cron.start_run("local-demo", "schedule")
    cron.finish_run(run_id, "FAILED", error=CIRCUIT_ERR)
    engine = _engine(iso_engine, cron_db=str(cron.db_path))
    signals = engine.collect()
    cron_fails = [s for s in signals if s.kind == "task_failure" and s.resource == "cron:local-demo"]
    assert len(cron_fails) == 1
    assert CIRCUIT_ERR in cron_fails[0].meta["error"]


def test_collect_discrepancies(tmp_path: Path, iso_engine: dict) -> None:
    store = DiscrepancyStore(db_path=str(tmp_path / "disc.db"))
    store.insert(
        Discrepancy(
            id="d1", timestamp=_hours_ago(1), subsystem="automation_audit",
            expected_state="provider within bounds", observed_state="MSB_ZAPIER_API_KEY not set",
            discrepancy_type="provider_unavailable", severity="warn", evidence={},
            confidence=1.0, affected_resource="zapier", suggested_action="resolve",
        )
    )
    engine = _engine(iso_engine, discrepancy_store=store)
    signals = engine.collect()
    disc = [s for s in signals if s.kind == "discrepancy"]
    assert len(disc) == 1
    assert disc[0].resource == "zapier"


def test_collect_boot_restarts(tmp_path: Path, iso_engine: dict) -> None:
    chain = AuditChain(db_path=str(tmp_path / "audit.db"), allow_keyless=True)
    chain.append("boot", "boot.started", {"pid": 1})
    engine = _engine(iso_engine, chain=chain)
    signals = engine.collect()
    restarts = [s for s in signals if s.kind == "restart"]
    assert len(restarts) == 1
    assert restarts[0].resource == "msb_v3_server"


# --- correlation + diagnosis -----------------------------------------------


def test_diagnose_full_incident(tmp_path: Path, iso_engine: dict) -> None:
    """The canonical pattern: DeepSeek 402 storm → wake + cron failures →
    backlog → restart. Roots must rank deepseek first with a causal chain."""
    wake = WakeStore(db_path=str(tmp_path / "wake.db"))
    _seed_wake_failures(wake, n=5)
    wake.post("stuck", sender="test")
    cron = CronStore(db_path=str(tmp_path / "cron.db"))
    cron.create_job("local-demo", "local demo", "* * * * *", {"type": "demo"}, enabled=True)
    run_id = cron.start_run("local-demo", "schedule")
    cron.finish_run(run_id, "FAILED", error=CIRCUIT_ERR)
    chain = AuditChain(db_path=str(tmp_path / "audit.db"), allow_keyless=True)
    chain.append("boot", "boot.started", {"pid": 1})

    engine = _engine(
        iso_engine, wake_db=str(wake.db_path), cron_db=str(cron.db_path), chain=chain
    )
    report = engine.diagnose()
    assert report["ok"] is True
    assert report["signal_count"] >= 4  # provider failure, backlog, cron fail, restart
    roots = report["roots"]
    assert roots, "must rank at least one root"
    top = roots[0]
    assert top["resource"] == "deepseek"
    assert top["kind"] == "provider_outage"
    assert top["confidence"] >= 0.8
    # The causal chain reaches the cron job and the wake inbox.
    assert any("cron:local-demo" in e["effect"] for e in top["chain"])
    assert any("wake_inbox" in e["effect"] for e in top["chain"])
    # Restart follows the storm (R4) when boot signals exist.
    assert any(e["relation"] == "follows" and e["effect"] == "msb_v3_server" for e in report["edges"])


def test_diagnose_quiet_system(iso_engine: dict) -> None:
    report = _engine(iso_engine).diagnose()
    assert report["ok"] is True
    assert report["signal_count"] == 0
    assert report["roots"] == []


def test_diagnose_ranks_isolated_provider_below_corroborated(tmp_path: Path, iso_engine: dict) -> None:
    """A provider with only observed failures ranks below one corroborated by
    an open discrepancy (hard evidence agrees with observed failures)."""
    wake = WakeStore(db_path=str(tmp_path / "wake.db"))
    _seed_wake_failures(wake, n=2, error="ConnectionError: ollama unreachable")
    _seed_wake_failures(wake, n=2, error=CIRCUIT_ERR)
    disc = DiscrepancyStore(db_path=str(tmp_path / "disc.db"))
    disc.insert(
        Discrepancy(
            id="d1", timestamp=_hours_ago(1), subsystem="automation_audit",
            expected_state="ok", observed_state="MSB_DEEPSEEK not set",
            discrepancy_type="provider_unavailable", severity="warn", evidence={},
            confidence=1.0, affected_resource="deepseek", suggested_action="resolve",
        )
    )
    engine = _engine(iso_engine, wake_db=str(wake.db_path), discrepancy_store=disc)
    report = engine.diagnose()
    resources = [r["resource"] for r in report["roots"]]
    assert resources[0] == "deepseek"
    assert "ollama" in resources
    assert report["roots"][0]["confidence"] > report["roots"][1]["confidence"]


def test_reason_seam_is_deterministic(tmp_path: Path, iso_engine: dict) -> None:
    wake = WakeStore(db_path=str(tmp_path / "wake.db"))
    _seed_wake_failures(wake, n=1)
    engine = _engine(iso_engine, wake_db=str(wake.db_path))
    report = engine.diagnose()
    narrative = engine.reason(report)
    assert "deepseek" in narrative
    assert "confidence=" in narrative
