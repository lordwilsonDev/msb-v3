"""Guard unit tests — the flywheel's single enforcement point."""

from __future__ import annotations

import pytest

from msb_v3.governance.approval import ApprovalQueue
from msb_v3.governance.budget import BudgetLedger
from msb_v3.governance.governor import OuroborosGovernor
from msb_v3.governance.guard import Guard
from msb_v3.governance.killswitch import KillSwitch
from msb_v3.uac.audit_chain import AuditChain


@pytest.fixture()
def env(tmp_path):
    chain = AuditChain(db_path=str(tmp_path / "audit.db"))
    ledger = BudgetLedger(
        db_path=str(tmp_path / "budget.db"),
        limits={"research_calls": 1, "tokens": 100, "iterations": 10},
    )
    queue = ApprovalQueue(db_path=str(tmp_path / "appr.db"), audit_chain=chain)
    switch = KillSwitch(db_path=str(tmp_path / "ks.db"), audit_chain=chain)
    governor = OuroborosGovernor(db_path=str(tmp_path / "gov.db"))
    guard = Guard(switch, ledger, queue, governor, audit_chain=chain)
    return {
        "guard": guard, "ledger": ledger, "queue": queue,
        "switch": switch, "chain": chain,
    }


def test_all_clear_passes(env) -> None:
    verdict = env["guard"].check_run(action="charge")
    assert verdict.allowed is True
    assert verdict.action == "OK"


def test_kill_switch_halts(env) -> None:
    env["switch"].arm("wilson", "stop everything")
    verdict = env["guard"].check_run(action="charge")
    assert verdict.allowed is False
    assert verdict.action == "HALT"


def test_budget_exhaustion_halts(env) -> None:
    assert env["ledger"].spend("research_calls") is True  # limit is 1
    verdict = env["guard"].check_run(action="research", budget_units={"research_calls": 1})
    assert verdict.allowed is False
    assert verdict.action == "HALT"
    assert verdict.detail["category"] == "research_calls"


def test_approval_required_without_id(env) -> None:
    verdict = env["guard"].check_run(action="build-stage-7", kind="build")
    assert verdict.allowed is False
    assert verdict.action == "APPROVAL_REQUIRED"


def test_approval_pending(env) -> None:
    item = env["queue"].submit("build", "stage 7")
    verdict = env["guard"].check_run(action="build-stage-7", kind="build", approval_id=item.item_id)
    assert verdict.allowed is False
    assert verdict.action == "APPROVAL_PENDING"


def test_approved_passes(env) -> None:
    item = env["queue"].submit("build", "stage 7")
    env["queue"].approve(item.item_id, operator="wilson")
    verdict = env["guard"].check_run(action="build-stage-7", kind="build", approval_id=item.item_id)
    assert verdict.allowed is True
    assert verdict.action == "OK"


def test_wrong_kind_approval_id_rejected(env) -> None:
    build_item = env["queue"].submit("build", "stage 7")
    env["queue"].approve(build_item.item_id, operator="wilson")
    verdict = env["guard"].check_run(
        action="vault-write", kind="vault_write", approval_id=build_item.item_id,
    )
    assert verdict.allowed is False
    assert verdict.action == "APPROVAL_REQUIRED"


def test_rejected_approval_blocks(env) -> None:
    item = env["queue"].submit("git_commit", "commit")
    env["queue"].reject(item.item_id, operator="wilson", reason="not yet")
    verdict = env["guard"].check_run(action="commit", kind="git_commit", approval_id=item.item_id)
    assert verdict.allowed is False
    assert verdict.action == "APPROVAL_REQUIRED"


def test_governor_halt_surfaces(env) -> None:
    g = env["guard"]
    # Default governor stall_limit is 6; feed 6 low-novelty signals, then a 7th.
    for i in range(6):
        g.check_run(
            action="charge",
            signal={"proposal_id": f"p{i}", "novelty": 0.01},
        )
    verdict = g.check_run(
        action="charge",
        signal={"proposal_id": "p6", "novelty": 0.01},
    )
    assert verdict.allowed is False
    assert verdict.action == "HALT"


def test_governor_slow_allows(env) -> None:
    g = env["guard"]
    verdict = None
    for i, nv in enumerate((0.9, 0.85, 0.8, 0.9, 0.7, 0.6)):
        verdict = g.check_run(action="charge", signal={"proposal_id": f"p{i}", "novelty": nv})
    assert verdict is not None
    assert verdict.allowed is True
    assert verdict.action == "SLOW"


def test_refusals_audited(env) -> None:
    env["guard"].check_run(action="build-stage-7", kind="build")
    chain = env["chain"]
    records = chain.get_chain(component="governance")
    assert len(records) == 1
    assert records[0].event_type == "blocked"
    assert records[0].payload["action"] == "APPROVAL_REQUIRED"


def test_record_action_appends(env) -> None:
    env["guard"].record_action("loop", "executed", {"step": "charge", "run": "r1"})
    records = env["chain"].get_chain(component="loop")
    assert [r.event_type for r in records] == ["executed"]
