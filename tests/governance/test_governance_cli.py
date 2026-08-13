"""CLI tests: python -m msb_v3.governance config.

Pins the operator-visible config surface — the human-readable lines and
the --json output — against the shared guard_config() builder, which is
the same source /system/config serves from. If the CLI and the endpoint
drift, guard_config() is the single point of truth to fix.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import msb_v3.uac.audit_chain as audit_chain
from msb_v3.core.config import settings
from msb_v3.core.guard_config import guard_config
from msb_v3.governance.approval import ApprovalQueue
from msb_v3.governance.cli import main


@pytest.fixture()
def iso_governance(tmp_path, monkeypatch) -> None:
    """Point every governance singleton's DB at tmp so the CLI commands
    never touch real brake state (kill switch, approvals, governor
    history) on the machine running the suite.

    settings.db_path is read at call time by default_db_path() (kill
    switch / approvals / governor), but audit_chain's default DB is
    computed at MODULE IMPORT — so it must be patched explicitly or the
    arm/disarm/approve/reject audit writes would land in the real chain.
    """
    monkeypatch.setattr(settings, "db_path", str(tmp_path / "msb_v3.db"))
    monkeypatch.setattr(
        audit_chain, "_AUDIT_DB", Path(tmp_path) / "uac" / "audit_chain.db"
    )


def test_config_exits_zero_and_prints_blocks(capsys) -> None:
    assert main(["config"]) == 0
    out = capsys.readouterr().out
    # budget caps, governor thresholds, approval policy, flywheel mechanics,
    # and the /v1 rate guards all show up as human-readable lines
    assert "[governance] budget caps per rolling window:" in out
    assert "tokens: " in out and "iterations: " in out
    assert "[governance] governor thresholds:" in out
    assert "[governance] approval kinds: build, combine" in out
    assert "[governance] approval stages:" in out
    assert "[flywheel] stages (9):" in out
    assert "[flywheel] iterations per stage: 1" in out
    assert "[flywheel] research-call spenders: charge, scan_papers" in out
    assert "[rate] chat:" in out and "embed:" in out


def test_config_json_is_identical_to_shared_builder(capsys) -> None:
    """--json must emit the verbatim guard_config() blocks — the exact
    shape /system/config serves, so a CLI diff against the endpoint is a
    true parity check."""
    assert main(["config", "--json"]) == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed == guard_config()
    assert set(parsed) == {"rate_limits", "governance", "approvals", "flywheel"}


def test_status_fresh_state(iso_governance, capsys) -> None:
    """Fresh state: disarmed switch, empty budgets, no pending approvals,
    no governor signals — the cold-start branch of cmd_status."""
    assert main(["status"]) == 0
    out = capsys.readouterr().out
    assert "killswitch: disarmed" in out
    assert "[governance] budget research_calls:" in out
    assert "[governance] approvals pending: 0" in out
    assert "[governance] governor: no signals yet" in out


def test_budget_prints_all_categories(iso_governance, capsys) -> None:
    assert main(["budget"]) == 0
    out = capsys.readouterr().out
    for cat in ("research_calls", "tokens", "iterations"):
        assert f"[governance] budget {cat}:" in out


def test_arm_then_disarm(iso_governance, capsys) -> None:
    assert main(["arm", "runaway loop"]) == 0
    out = capsys.readouterr().out
    assert "kill switch ARMED by cli (reason: runaway loop)" in out
    assert main(["disarm"]) == 0
    out = capsys.readouterr().out
    assert "kill switch disarmed by cli" in out


def test_approvals_empty(iso_governance, capsys) -> None:
    assert main(["approvals"]) == 0
    assert "approvals (all): 0" in capsys.readouterr().out


def test_approve_happy_path_and_idempotency(iso_governance, capsys) -> None:
    # Seed a PENDING item into the same (tmp) store the CLI reads.
    queue = ApprovalQueue()
    item = queue.submit("build", "seed build item")
    assert main(["approve", item.item_id, "--operator", "tester"]) == 0
    out = capsys.readouterr().out
    assert f"approved build {item.item_id}" in out
    # A second approve of the same id is an idempotency error -> rc 1.
    assert main(["approve", item.item_id, "--operator", "tester"]) == 1
    err = capsys.readouterr().err
    assert "ERROR:" in err and "already" in err


def test_approve_unknown_item_fails(iso_governance, capsys) -> None:
    assert main(["approve", "does-not-exist"]) == 1
    err = capsys.readouterr().err
    assert "ERROR:" in err and "unknown approval item" in err


def test_reject_requires_reason(iso_governance, capsys) -> None:
    queue = ApprovalQueue()
    item = queue.submit("build", "seed build item")
    assert main(["reject", item.item_id, ""]) == 1
    err = capsys.readouterr().err
    assert "ERROR:" in err and "requires a reason" in err


def test_reject_happy_path(iso_governance, capsys) -> None:
    queue = ApprovalQueue()
    item = queue.submit("build", "seed build item")
    assert main(["reject", item.item_id, "not now"]) == 0
    out = capsys.readouterr().out
    assert f"rejected build {item.item_id}" in out
