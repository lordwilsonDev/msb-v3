"""BudgetLedger unit tests — caps halt work (fail-closed)."""

from __future__ import annotations

import sqlite3

import pytest

from msb_v3.governance.budget import BudgetLedger


@pytest.fixture()
def ledger(tmp_path) -> BudgetLedger:
    return BudgetLedger(
        db_path=str(tmp_path / "budget.db"),
        limits={"research_calls": 2, "tokens": 10, "iterations": -1},
        window_s=3600,
    )


def test_cap_halt(ledger: BudgetLedger) -> None:
    assert ledger.spend("research_calls") is True
    assert ledger.spend("research_calls") is True
    assert ledger.spend("research_calls") is False
    st = ledger.state()["research_calls"]
    assert st["spent"] == 2
    assert st["remaining"] == 0


def test_zero_cap_denies_everything(tmp_path) -> None:
    zero = BudgetLedger(db_path=str(tmp_path / "b.db"), limits={"tokens": 0}, window_s=3600)
    assert zero.spend("tokens", 1) is False
    assert zero.spend("tokens", 500) is False
    assert zero.state()["tokens"]["spent"] == 0


def test_negative_limit_is_unlimited(tmp_path) -> None:
    uncapped = BudgetLedger(db_path=str(tmp_path / "b.db"), limits={"iterations": -1}, window_s=3600)
    for _ in range(1000):
        assert uncapped.spend("iterations") is True
    st = uncapped.state()["iterations"]
    assert st["limit"] == -1
    assert st["remaining"] == -1


def test_unknown_category_is_unlimited(tmp_path) -> None:
    empty = BudgetLedger(db_path=str(tmp_path / "b.db"), limits={}, window_s=3600)
    assert empty.spend("brand_new_category") is True


def test_window_rollover_resets(tmp_path) -> None:
    one = BudgetLedger(db_path=str(tmp_path / "b.db"), limits={"research_calls": 1}, window_s=3600)
    assert one.spend("research_calls") is True
    assert one.spend("research_calls") is False
    with sqlite3.connect(one.db_path) as conn:
        conn.execute("UPDATE budget_entries SET period_start = period_start - 7200")
    assert one.spend("research_calls") is True  # fresh window


def test_persistence_across_instances(tmp_path) -> None:
    p = str(tmp_path / "b.db")
    first = BudgetLedger(db_path=p, limits={"tokens": 10}, window_s=3600)
    assert first.spend("tokens", 7) is True
    second = BudgetLedger(db_path=p, limits={"tokens": 10}, window_s=3600)
    assert second.state()["tokens"]["spent"] == 7
    assert second.spend("tokens", 3) is True
    assert second.spend("tokens", 1) is False


def test_reset_clears(tmp_path) -> None:
    five = BudgetLedger(db_path=str(tmp_path / "b.db"), limits={"research_calls": 5}, window_s=3600)
    five.spend("research_calls", 4)
    five.reset()
    assert five.state()["research_calls"]["spent"] == 0
    assert five.spend("research_calls") is True
