"""KillSwitch unit tests — one control to pause the loop, fail-closed."""

from __future__ import annotations

import pytest

from msb_v3.governance.killswitch import GovernanceHalt, KillSwitch
from msb_v3.uac.audit_chain import AuditChain


@pytest.fixture()
def switch(tmp_path) -> KillSwitch:
    return KillSwitch(
        db_path=str(tmp_path / "ks.db"),
        audit_chain=AuditChain(db_path=str(tmp_path / "audit.db")),
    )


def test_arm_disarm(switch: KillSwitch) -> None:
    assert switch.is_armed() is False
    st = switch.arm("wilson", "runaway loop")
    assert st["armed"] is True
    assert st["reason"] == "runaway loop"
    assert switch.is_armed() is True
    switch.disarm("wilson")
    assert switch.is_armed() is False


def test_arm_disarm_audited(switch: KillSwitch, tmp_path) -> None:
    switch.arm("wilson", "test")
    switch.disarm("wilson")
    chain = AuditChain(db_path=str(tmp_path / "audit.db"))
    records = chain.get_chain(component="killswitch")
    assert [r.event_type for r in records] == ["armed", "disarmed"]


def test_fail_closed_armed_on_unreadable_state(switch: KillSwitch, tmp_path) -> None:
    switch.arm("wilson", "x")
    db = tmp_path / "ks.db"
    db.unlink()
    db.mkdir()  # state unreadable -> must read as armed
    assert switch.is_armed() is True
    st = switch.state()
    assert st["armed"] is True
    assert st.get("fail_closed") is True


def test_require_allowed(switch: KillSwitch) -> None:
    switch.require_allowed()  # no raise when disarmed
    switch.arm("wilson", "halt")
    with pytest.raises(GovernanceHalt):
        switch.require_allowed()


def test_persistence_across_restarts(tmp_path) -> None:
    p = str(tmp_path / "ks.db")
    a = KillSwitch(db_path=p, audit_chain=AuditChain(db_path=str(tmp_path / "audit.db")))
    a.arm("wilson", "persist me")
    b = KillSwitch(db_path=p, audit_chain=AuditChain(db_path=str(tmp_path / "audit.db")))
    assert b.is_armed() is True  # a restart must never clear the switch
