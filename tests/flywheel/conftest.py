"""Shared fixtures for the flywheel suite — every brake is tmp-backed."""

from __future__ import annotations

import pytest

from msb_v3.flywheel.engine import FlywheelEngine
from msb_v3.governance.approval import ApprovalQueue
from msb_v3.governance.budget import BudgetLedger
from msb_v3.governance.governor import OuroborosGovernor
from msb_v3.governance.killswitch import KillSwitch
from msb_v3.uac.audit_chain import AuditChain
from msb_v3.uac.axiom_library import AxiomLibrary


@pytest.fixture()
def env(tmp_path):
    """(engine, queue, chain, tmp_path) — all brake singletons + stores
    isolated under tmp_path so a test can rebuild the same paths to prove
    restart survival."""
    chain = AuditChain(db_path=str(tmp_path / "audit.db"))
    queue = ApprovalQueue(db_path=str(tmp_path / "appr.db"), audit_chain=chain)
    ledger = BudgetLedger(
        db_path=str(tmp_path / "budget.db"),
        limits={"research_calls": 10, "tokens": 1000, "iterations": 50},
        window_s=3600,
    )
    switch = KillSwitch(db_path=str(tmp_path / "ks.db"), audit_chain=chain)
    governor = OuroborosGovernor(db_path=str(tmp_path / "gov.db"))
    engine = FlywheelEngine(
        db_path=str(tmp_path / "turns.db"),
        queue=queue,
        ledger=ledger,
        switch=switch,
        governor=governor,
        audit_chain=chain,
        axiom_library=AxiomLibrary(db_path=str(tmp_path / "axiom.db")),
        vault_root=tmp_path / "vault",
        runtime_root=tmp_path / "rt",
        novelty_threshold=0.85,
        novelty_fn=lambda problem: 0.0,  # hermetic: never depends on the live vault
    )
    return {"engine": engine, "queue": queue, "chain": chain, "tmp_path": tmp_path}


@pytest.fixture()
def rebuild(env):
    """Build a fresh engine over the SAME paths — the restart-survival proof."""

    def _rebuild(**overrides) -> FlywheelEngine:
        p = env["tmp_path"]
        chain = AuditChain(db_path=str(p / "audit.db"))
        base = dict(
            db_path=str(p / "turns.db"),
            queue=ApprovalQueue(db_path=str(p / "appr.db"), audit_chain=chain),
            ledger=BudgetLedger(
                db_path=str(p / "budget.db"),
                limits={"research_calls": 10, "tokens": 1000, "iterations": 50},
                window_s=3600,
            ),
            switch=KillSwitch(db_path=str(p / "ks.db"), audit_chain=chain),
            governor=OuroborosGovernor(db_path=str(p / "gov.db")),
            audit_chain=chain,
            axiom_library=AxiomLibrary(db_path=str(p / "axiom.db")),
            vault_root=p / "vault",
            runtime_root=p / "rt",
            novelty_fn=lambda problem: 0.0,  # hermetic: never depends on the live vault
        )
        base.update(overrides)
        return FlywheelEngine(**base)

    return _rebuild
