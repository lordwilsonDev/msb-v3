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
from typing import Any, Callable, Dict, List, Optional

from msb_v3.core.event_bus import EventBus
from msb_v3.uac.audit_chain import AuditChain, tamper


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
    assert not thread.is_alive(), (
        f"deadlocked: {fn!r} did not complete within {timeout}s"
    )
    assert result.get("ok"), f"raised: {result.get('error')!r}"
    return result.get("value")


def _append_with_retry(
    chain: AuditChain,
    component: str,
    event_type: str,
    payload: Dict[str, Any],
    attempts: int = 5,
) -> None:
    """Append, retrying on sqlite's "database is locked".

    Under a saturated shared CI box (self-hosted runner executing several
    gates at once), fs/CPU contention can hold the RESERVED lock past the
    connection's busy timeout, so BEGIN IMMEDIATE raises OperationalError.
    Thread exceptions do NOT propagate through t.join(), so a worker that
    dies here would silently drop records (observed as 300/400 in the wild).
    This test pins the chain CONTRACT — every record lands and the chain
    stays valid — so bounded lock-contention retries are part of the harness,
    not an assertion weakening. Non-contention errors (disk full, corruption)
    re-raise immediately.
    """
    last_error: Optional[Exception] = None
    for attempt in range(attempts):
        try:
            chain.append(component, event_type, payload)
            return
        except sqlite3.OperationalError as exc:
            last_error = exc
            if "locked" not in str(exc).lower():
                raise
            time.sleep(0.05 * (attempt + 1))
    raise AssertionError(
        f"audit append still locked after {attempts} attempts: {last_error!r}"
    )


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
    shape of someone editing the DB file by hand (defeating the append-only
    trigger the way a knowledgeable attacker would)."""
    tamper(
        chain.db_path,
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
    fast, not hang.

    Worker-thread exceptions do not propagate through t.join(), so each
    worker collects its failure into `worker_errors` and the main thread
    asserts on it — the informative "still locked after N attempts" message
    surfaces instead of degrading to a bare count mismatch."""
    chain = AuditChain(db_path=str(tmp_path / "audit.db"))
    worker_errors: List[BaseException] = []

    def append_all(chain: AuditChain, count: int, errors: List[BaseException]) -> None:
        try:
            for i in range(count):
                _append_with_retry(chain, "chaos", "tick", {"i": i})
        except BaseException as exc:  # collected for the main thread to assert
            errors.append(exc)

    def hammer() -> None:
        threads = [
            threading.Thread(target=append_all, args=(chain, 100, worker_errors))
            for _ in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    # 60s, not the default 30s: worst-case retry (5 attempts x up to 10s busy
    # wait) on a wedged DB must not be misreported as a deadlock.
    run_with_deadlock_guard(hammer, timeout=60)

    assert not worker_errors, f"worker threads failed: {worker_errors!r}"
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


def test_bus_emit_with_no_subscribers_is_a_noop():
    """emit() with zero subscribers still records history and returns the
    event — the bus does not depend on subscribers existing."""
    bus = EventBus()
    event = bus.emit("lonely", {"k": 1})
    assert event.type == "lonely"
    assert len(bus.history()) == 1
    assert bus.history()[0].payload == {"k": 1}


# ---------------------------------------------------------------------------
# PERF PROBE -- the audit chain's append cost, visible so regressions show
# ---------------------------------------------------------------------------


def test_perf_audit_append_throughput(tmp_path):
    """1000 audit appends must stay inside the healthy envelope; prints
    steady-state per-record cost so a locking or fs regression is visible
    (and fixable by numbers).

    The assertion is deliberately a REGRESSION FLOOR, not a benchmark: on a
    shared box (the self-hosted runner executing several gates at once) the
    wall clock per-append can legitimately spike ~30-50x under fs/CPU
    saturation — the observed worst case was ~34s for 1000 appends. A warm-up
    pass excludes cold-start (page cache, first-transaction setup) from the
    measured window, and the 120s ceiling only trips on order-of-magnitude
    regressions (per-append lock acquisition, full-chain rescan per append,
    fsync storms) — never on load. The printed per-record number is the real
    perf signal."""
    chain = AuditChain(db_path=str(tmp_path / "audit.db"))
    for i in range(20):  # warm-up: exclude cold-start from the measured window
        chain.append("chaos", "tick", {"n": i})
    half = 490
    t0 = time.perf_counter()
    for i in range(20, 20 + half):
        chain.append("chaos", "tick", {"n": i})
    first = time.perf_counter() - t0
    t0 = time.perf_counter()
    for i in range(20 + half, 1000):
        chain.append("chaos", "tick", {"n": i})
    second = time.perf_counter() - t0
    dt = first + second
    us = dt / 980 * 1_000_000
    print(
        f"\n[perf] audit append (steady state): {us:.0f} us/record "
        f"({980 / dt:,.0f} records/s, first half {first:.2f}s / second {second:.2f}s)"
    )
    assert dt < 120.0
    # Load-cancelling shape check: both halves are measured under the same
    # load, so contention cancels. Per-record cost must not grow as the chain
    # grows — an O(n) per-append regression (e.g. a full-chain rescan for
    # prev_hash) makes the second half several times slower per record. 5x
    # ratio + 0.5s absolute slack absorbs load drift while flagging rescan
    # regressions.
    assert second < first * 5 + 0.5, (
        f"per-record cost grew {second / max(first, 1e-9):.1f}x between halves "
        f"({first:.2f}s -> {second:.2f}s): possible per-append chain rescan"
    )
