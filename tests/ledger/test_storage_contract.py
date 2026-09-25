from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import pytest

from msb_ledger.storage_contract import (
    AuditWriteBlocked,
    StorageContract,
    StorageProbe,
    StorageState,
)
from msb_v3.uac.audit_chain import AuditChain


def _contract(tmp_path: Path, free: int, total: int = 100) -> StorageContract:
    return StorageContract(
        tmp_path / "audit.db",
        low_ratio=0.15,
        critical_ratio=0.10,
        blocked_ratio=0.05,
        probe=lambda _path: StorageProbe(free_bytes=free, total_bytes=total),
        enabled=True,
    )


def test_connection_contract_sets_explicit_sqlite_pragmas(tmp_path: Path) -> None:
    db = tmp_path / "audit.db"
    with sqlite3.connect(db) as conn:
        StorageContract.configure_connection(conn)
        settings = StorageContract.effective_settings(conn)
    assert settings["journal_mode"].lower() == "wal"
    assert settings["synchronous"] == 2
    assert settings["foreign_keys"] == 1
    assert settings["busy_timeout"] == 10000
    assert settings["wal_autocheckpoint"] == 1000
    assert settings["cache_size"] == -4096


def test_storage_thresholds_persist_and_clear(tmp_path: Path) -> None:
    contract = _contract(tmp_path, free=20)
    assert contract.state() is StorageState.NORMAL

    contract = _contract(tmp_path, free=14)
    assert contract.state() is StorageState.LOW_SPACE
    assert contract.health()["state"] == "LOW_SPACE"

    contract = _contract(tmp_path, free=9)
    assert contract.state() is StorageState.CRITICAL_SPACE

    contract = _contract(tmp_path, free=4)
    assert contract.state() is StorageState.AUDIT_WRITE_BLOCKED
    with pytest.raises(AuditWriteBlocked):
        contract.assert_write_allowed()
    assert contract.health()["write_allowed"] is False

    contract.clear_block()
    assert contract.state() is StorageState.NORMAL
    contract.assert_write_allowed()


def test_blocked_state_is_fail_closed_and_durable(tmp_path: Path) -> None:
    contract = _contract(tmp_path, free=100)
    contract.mark_blocked("disk full")
    reopened = _contract(tmp_path, free=100)
    assert reopened.state() is StorageState.AUDIT_WRITE_BLOCKED
    with pytest.raises(AuditWriteBlocked):
        reopened.assert_write_allowed()
    assert json_state(reopened)["reason"] == "disk full"

    reopened.mark_safe_mode("operator investigation")
    assert reopened.state() is StorageState.SAFE_MODE
    with pytest.raises(AuditWriteBlocked):
        reopened.assert_write_allowed()


def json_state(contract: StorageContract) -> dict[str, str]:
    return json.loads(contract.state_path.read_text())


def test_audit_append_marks_blocked_after_disk_full(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MSB_STORAGE_CONTRACT_ENABLED", "1")
    chain = AuditChain(str(tmp_path / "audit.db"), allow_keyless=True)

    def fail_connection() -> sqlite3.Connection:
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(chain, "_conn", fail_connection)
    with pytest.raises(AuditWriteBlocked):
        chain.append("test", "event", {"ok": True})
    assert chain.storage_health()["state"] == "AUDIT_WRITE_BLOCKED"
    assert chain.storage_health()["write_allowed"] is False


def test_operator_clear_is_a_bounded_window(tmp_path: Path) -> None:
    """The clear suspends the refusal, then the refusal comes back on its own."""
    contract = _contract(tmp_path, free=4)
    assert contract.state() is StorageState.AUDIT_WRITE_BLOCKED

    expires_at = contract.clear_block("operator replaced the volume", ttl_s=0.05)
    assert contract.state() is StorageState.NORMAL
    contract.assert_write_allowed()

    health = contract.health()
    assert health["override_active"] is True
    assert datetime.fromisoformat(health["override_expires_at"]) == expires_at
    # The window never hides the underlying condition from an observer.
    assert health["derived_state"] == "AUDIT_WRITE_BLOCKED"

    time.sleep(0.1)
    assert contract.state() is StorageState.AUDIT_WRITE_BLOCKED
    with pytest.raises(AuditWriteBlocked):
        contract.assert_write_allowed()
    assert contract.health()["override_active"] is False


def test_explicit_latch_outranks_a_live_override(tmp_path: Path) -> None:
    contract = _contract(tmp_path, free=100)
    contract.clear_block("cleared", ttl_s=300)
    contract.mark_blocked("database or disk is full")
    assert contract.state() is StorageState.AUDIT_WRITE_BLOCKED
    with pytest.raises(AuditWriteBlocked):
        contract.assert_write_allowed()


def test_unparseable_override_does_not_grant_a_bypass(tmp_path: Path) -> None:
    """A corrupt expiry must fail towards the refusal, never towards the window."""
    contract = _contract(tmp_path, free=4)
    contract.clear_block("cleared", ttl_s=300)
    contract.state_path.write_text(
        json.dumps({"state": "NORMAL", "reason": "x", "override_expires_at": "not-a-date"}),
        encoding="utf-8",
    )
    assert contract.state() is StorageState.AUDIT_WRITE_BLOCKED


def test_operator_clear_refuses_a_non_positive_window(tmp_path: Path) -> None:
    contract = _contract(tmp_path, free=4)
    with pytest.raises(ValueError):
        contract.clear_block(ttl_s=0)
