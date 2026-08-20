"""Tests for the automation budget ledger (automation/budget.py)."""

from __future__ import annotations

import pytest

from msb_v3.automation.budget import BudgetLedger


def test_record_and_status(tmp_path) -> None:
    ledger = BudgetLedger(db_path=str(tmp_path / "budget.db"), cap_usd=10.0)
    assert ledger.status() == {"cap_usd": 10.0, "spent_usd": 0.0, "remaining_usd": 10.0}
    ledger.record(0.001, kind="llm_plan", provider="n8n")
    assert ledger.spent() == pytest.approx(0.001)
    assert ledger.remaining() == pytest.approx(9.999)


def test_cap_is_hard(tmp_path) -> None:
    ledger = BudgetLedger(db_path=str(tmp_path / "budget.db"), cap_usd=0.01)
    ledger.record(0.008)
    with pytest.raises(ValueError):
        ledger.record(0.003)  # would exceed the cap
    assert ledger.spent() == pytest.approx(0.008)


def test_check_within_cap(tmp_path) -> None:
    ledger = BudgetLedger(db_path=str(tmp_path / "budget.db"), cap_usd=1.0)
    assert ledger.check(0.5) is True
    ledger.record(0.5)
    assert ledger.check(0.6) is False  # 0.5 + 0.6 > 1.0
    assert ledger.check(0.5) is True  # exactly at the cap is allowed


def test_negative_entries_rejected(tmp_path) -> None:
    ledger = BudgetLedger(db_path=str(tmp_path / "budget.db"), cap_usd=1.0)
    with pytest.raises(ValueError):
        ledger.record(-0.5)
