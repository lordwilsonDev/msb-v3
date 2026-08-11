"""Chaos suite for the Sovereign Runtime (phase 2) — the Engineering Hygiene
Battery themes the phase-1 suite does not yet touch.

Phase 1 pinned the event bus deadlock/reentrancy shape, handler failure
modes, liveness, concurrency, planner bounds, and perf probes. Phase 2
extends the same unit-level, deterministic, sync-only adversarial style to
three more battery experiments (see
~/Documents/Vault/50_Experiments/workflows/msb-v3-engineering-hygiene-battery.md):

- H03 IDEMPOTENCY: emitting/delivering/planning the same thing twice must
  behave identically the second time — no state creep, no surprise dedup.
  The bus contract is "no dedup" (consumers own idempotency); the planner
  contract is "identical serialized trees"; the audit chain's verify is a
  pure read.
- H06 AUDIT-CHAIN TAMPERING (at scale): a hash chain of 500 records with the
  middle, genesis-adjacent, or tail record edited directly in SQLite must be
  detected at the exact break point, quarantined, repaired, and re-verified
  — including the adversarial ordering where appends land AFTER the tamper,
  and concurrent appends from many threads. Known limitation (not covered):
  repair() itself reads on one connection and rewrites on another, so a
  concurrent append between those steps could still fork a chain being
  repaired — operator-controlled, flagged here so it is a conscious gap.
- H09 DEPENDENCY SUBTRACTION: removing a dependency (a raising memory
  backend, a missing YAML file, absent subscribers) must fail loudly or
  degrade to defaults — never hang, never partially mutate.

Everything is deterministic and sync-only so the suite runs green under the
STRICT pytest-asyncio config with no event loop involved. Threaded tests use
the deadlock guard (the repo has no per-test timeout), so a lock regression
fails fast instead of hanging CI. Failure in any of these = a regression to
investigate, not a flake.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict

import sovereign_runtime.config as config_module
from msb_v3.uac.audit_chain import AuditChain
from sovereign_runtime import EventBus
from sovereign_runtime.brain import BrainService
from sovereign_runtime.brain.recursive_planner import RecursivePlanner
from sovereign_runtime.config import get, load_config


def run_with_deadlock_guard(fn: Callable[[], Any], timeout: float = 10.0) -> Any:
    """Run fn in a worker thread and join(timeout) — a regression to a
    non-reentrant lock would otherwise hang pytest instead of failing it."""
    result: Dict[str, Any] = {}

    def worker() -> None:
        try:
            result["value"] = fn()
            result["ok"] = True
        except BaseException as exc:  # pragma: no cover -- only on failure
            result["error"] = exc

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(timeout)
    assert not thread.is_alive(), f"deadlocked: {fn!r} did not complete within {timeout}s"
    assert result.get("ok"), f"raised: {result.get('error')!r}"
    return result.get("value")


# ---------------------------------------------------------------------------
# H03 IDEMPOTENCY -- the second time must be exactly like the first
# ---------------------------------------------------------------------------


def test_same_event_emitted_twice_delivers_twice():
    """Pin the no-dedup contract: emitting the identical type+payload twice
    delivers twice and records both in history. Dedup is NOT the bus's job
    (consumers own idempotency) — a surprise dedup OR double-delivery shows
    up here as a contract diff."""
    bus = EventBus()
    delivered = []
    bus.subscribe("op", lambda e: delivered.append(e.payload))
    bus.emit("op", {"kind": "create", "id": 7})
    bus.emit("op", {"kind": "create", "id": 7})
    assert len(delivered) == 2
    assert [d["id"] for d in delivered] == [7, 7]
    assert len(bus.history()) == 2


def test_duplicate_subscription_delivers_twice_then_unsubscribe_once():
    """The same handler registered twice delivers twice per emit; unsubscribing
    removes one registration (list-remove semantics), leaving the other live."""
    bus = EventBus()
    calls = []
    handler = lambda e: calls.append(e.type)  # noqa: E731 -- shared handler
    bus.subscribe("x", handler)
    bus.subscribe("x", handler)
    bus.emit("x", {})
    assert len(calls) == 2
    bus.unsubscribe("x", handler)
    bus.emit("x", {})
    assert len(calls) == 3  # exactly one registration remains


def test_brain_same_goal_twice_emits_identical_plans():
    """Idempotency at the brain: the same goal emitted twice produces two
    plan.created events with byte-identical serialized plan trees. to_dict is
    deterministic — no node ids leak into the serialized form."""
    bus = EventBus()
    BrainService(bus=bus)
    plans = []
    bus.subscribe("agent.plan.created", lambda e: plans.append(e.payload["plan"]))
    for _ in range(2):
        bus.emit("agent.goal.received", {"goal": "ship the report"})
    assert len(plans) == 2
    assert plans[0] == plans[1]


def test_planner_identical_goals_deterministic_trees():
    """plan() on the same goal — simple and complex shapes — yields identical
    serialized trees across many calls: a deterministic planner contract."""
    planner = RecursivePlanner(memory=None)
    for goal in ("do the thing", "z" * 200):
        first = planner.to_dict(planner.plan(goal))
        for _ in range(10):
            assert planner.to_dict(planner.plan(goal)) == first


def test_config_get_is_stable_and_defaults_repeatable():
    """get() with the same path returns the same value every call; a missing
    path returns the same default every call — no state creep between reads.
    (Stability only — the actual value may be env/runtime.yaml overridden.)"""
    a = get("brain.framework")
    assert get("brain.framework") == a
    for _ in range(5):
        assert get("nope.missing", "fallback") == "fallback"


def test_audit_verify_is_idempotent_and_non_mutating(tmp_path):
    """verify_chain() is a pure read: repeated calls return the same verdict
    and never mutate record count, payloads, or hashes."""
    chain = AuditChain(db_path=str(tmp_path / "audit.db"))
    for i in range(25):
        chain.append("chaos", "tick", {"n": i})
    r1 = chain.verify_chain()
    r2 = chain.verify_chain()
    assert r1 == r2
    assert r1["valid"] is True and r1["record_count"] == 25
    assert len(chain.get_chain()) == 25


# ---------------------------------------------------------------------------
# H06 AUDIT-CHAIN TAMPERING -- at scale, at every position, through repair
# ---------------------------------------------------------------------------


def _tamper_seq(chain: AuditChain, seq: int) -> None:
    """Edit the stored payload directly in SQLite, bypassing append() — the
    shape of someone editing the DB file by hand."""
    with sqlite3.connect(chain.db_path) as conn:
        conn.execute(
            "UPDATE audit_records SET payload=? WHERE seq=?",
            (json.dumps({"n": "TAMPERED"}), seq),
        )


def test_audit_tamper_middle_of_large_chain_detected_and_repairable(tmp_path):
    """Scale + full recovery loop: 500 records, tamper #250. verify() breaks
    exactly at 250; quarantine marks it; repair() re-anchors; the chain
    verifies again; the auditable chain.repaired event is the tail."""
    chain = AuditChain(db_path=str(tmp_path / "audit.db"))
    for i in range(500):
        chain.append("stage_0", "event", {"n": i})
    _tamper_seq(chain, 250)

    verdict = chain.verify_chain()
    assert verdict["valid"] is False
    assert verdict["broken_at_seq"] == 250

    assert chain.quarantine()["quarantined"] is True
    assert chain._get_meta("state") == "quarantined"

    repair = chain.repair()
    assert repair["repaired"] is True
    assert repair["broken_at_seq"] == 250

    assert chain.verify_chain()["valid"] is True
    tail = chain.get_chain()[-1]
    assert tail.component == "chain"
    assert tail.event_type == "repaired"


def test_audit_tamper_genesis_record_breaks_entire_chain(tmp_path):
    """The first record is the anchor: editing it breaks every hash after it,
    and the break is reported at seq 1 (the whole chain is compromised).

    Pinned edge: a broken verdict carries NO record_count key — verify_chain()
    returns early on the first break, so the total record count is only
    available on the success path. Callers that always read record_count get
    a KeyError on tampering."""
    chain = AuditChain(db_path=str(tmp_path / "audit.db"))
    for i in range(100):
        chain.append("stage_0", "event", {"n": i})
    _tamper_seq(chain, 1)
    verdict = chain.verify_chain()
    assert verdict["valid"] is False
    assert verdict["broken_at_seq"] == 1
    assert verdict.get("record_count") is None  # only on the success path


def test_audit_tamper_tail_record_breaks_only_tail(tmp_path):
    """Editing only the newest record breaks the chain at the last seq and
    nowhere earlier."""
    chain = AuditChain(db_path=str(tmp_path / "audit.db"))
    for i in range(50):
        chain.append("stage_0", "event", {"n": i})
    _tamper_seq(chain, 50)
    verdict = chain.verify_chain()
    assert verdict["valid"] is False
    assert verdict["broken_at_seq"] == 50


def test_audit_appends_after_tamper_repair_rewrites_through_tail(tmp_path):
    """Adversarial ordering: tamper, then keep appending, THEN repair. repair()
    must rewrite from the first broken record THROUGH the post-tamper tail and
    end with the auditable repair event — appended-while-broken records must
    not silently escape the repair."""
    chain = AuditChain(db_path=str(tmp_path / "audit.db"))
    for i in range(20):
        chain.append("stage_0", "event", {"n": i})
    _tamper_seq(chain, 10)
    for i in range(20, 25):
        chain.append("stage_0", "event", {"n": i})  # appended while broken

    assert chain.verify_chain()["valid"] is False
    result = chain.repair()
    assert result["repaired"] is True
    assert result["broken_at_seq"] == 10

    records = chain.get_chain()
    assert len(records) == 26  # 20 + 5 post-tamper appends + 1 repair event
    assert chain.verify_chain()["valid"] is True
    assert records[-1].event_type == "repaired"


def test_audit_concurrent_appends_keep_chain_valid(tmp_path):
    """4 threads x 100 appends: sqlite serializes writes; the chain must stay
    valid and lose no records. Deadlock-guarded — a lock regression must fail
    fast, not hang."""
    chain = AuditChain(db_path=str(tmp_path / "audit.db"))

    def append_all(chain: AuditChain, count: int) -> None:
        for i in range(count):
            chain.append("chaos", "tick", {"i": i})

    def hammer() -> None:
        threads = [threading.Thread(target=append_all, args=(chain, 100)) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    run_with_deadlock_guard(hammer, timeout=30)

    verdict = chain.verify_chain()
    assert verdict["valid"] is True
    assert verdict["record_count"] == 400


def test_audit_pathological_payloads_stay_consistent(tmp_path):
    """Unicode, control chars, deep nesting, null bytes, and falsy values
    round-trip through append + verify without corrupting the chain."""
    chain = AuditChain(db_path=str(tmp_path / "audit.db"))
    payloads = [
        {"msg": "🚀🔥 " * 50},
        {"msg": "a\nb\tc\rd"},
        {"msg": "\x00\x01\x02"},
        {"nested": {"deep": {"deeper": ["x"] * 200}}},
        {"empty": "", "none": None, "zero": 0, "false": False},
    ]
    for payload in payloads:
        chain.append("chaos", "payload", payload)

    assert chain.verify_chain()["valid"] is True
    records = chain.get_chain()
    assert records[0].payload["msg"] == "🚀🔥 " * 50
    assert records[3].payload["nested"]["deep"]["deeper"][-1] == "x"
    assert records[4].payload["zero"] == 0


# ---------------------------------------------------------------------------
# H09 DEPENDENCY SUBTRACTION -- remove a dependency, fail loud or degrade
# ---------------------------------------------------------------------------


def test_planner_with_raising_memory_fails_loudly_not_silently():
    """A dead dependency (memory.record_plan_node raises) must PROPAGATE to
    the caller — fail loud, never hang, never return a half-built plan. After
    subtracting the dependency entirely (memory=None) the same goal plans
    cleanly: removal is safe, breakage is loud."""

    class BrokenMemory:
        def record_plan_node(self, **kwargs: Any) -> None:
            raise RuntimeError("memory backend unreachable")

    planner = RecursivePlanner(memory=BrokenMemory())
    raised = False
    try:
        planner.plan("goal")
    except RuntimeError:
        raised = True
    assert raised

    recovered = RecursivePlanner(memory=None)
    assert recovered.plan("goal").status in ("ready", "terminated")


def test_bus_emit_with_no_subscribers_is_a_noop():
    """emit() with zero subscribers still records history and returns the
    event — the bus does not depend on subscribers existing."""
    bus = EventBus()
    event = bus.emit("lonely", {"k": 1})
    assert event.type == "lonely"
    assert len(bus.history()) == 1
    assert bus.history()[0].payload == {"k": 1}


def test_config_missing_yaml_falls_back_to_defaults(monkeypatch):
    """Subtract the YAML dependency entirely: load_config() falls back to
    defaults and get() does not crash or return partial state. Env overrides
    are neutralized too, so the assertions pin the pure-defaults contract."""
    monkeypatch.setattr(config_module, "_CONFIG_PATH", Path("/nonexistent/runtime.yaml"))
    monkeypatch.setattr(config_module, "_env_overrides", lambda: {})
    cfg = load_config()
    assert cfg["brain"]["framework"] == "motia"
    assert cfg["safety"]["fail_closed"] is True
    assert get("brain.framework") == "motia"
    assert get("missing.path", "dflt") == "dflt"


def test_brain_survives_without_execute_consumers():
    """Subtract the downstream consumer (nothing subscribes to
    agent.execute.request): the brain's goal flow still completes, both
    events land in history, and the bus stays healthy."""
    bus = EventBus()
    BrainService(bus=bus)
    bus.emit("agent.goal.received", {"goal": "just plan"})
    assert len(bus.history("agent.plan.created")) == 1
    assert len(bus.history("agent.execute.request")) == 1
    assert bus.history()[-1].type == "agent.execute.request"


# ---------------------------------------------------------------------------
# PERF PROBE -- the audit chain's append cost, visible so regressions show
# ---------------------------------------------------------------------------


def test_perf_audit_append_throughput(tmp_path):
    """1000 audit appends must complete quickly; prints per-record cost so a
    locking or fs regression is visible (and fixable by numbers)."""
    chain = AuditChain(db_path=str(tmp_path / "audit.db"))
    t0 = time.perf_counter()
    for i in range(1000):
        chain.append("chaos", "tick", {"n": i})
    dt = time.perf_counter() - t0
    us = dt / 1000 * 1_000_000
    print(f"\n[perf] audit append: {us:.0f} us/record ({1000 / dt:,.0f} records/s)")
    assert dt < 10.0
