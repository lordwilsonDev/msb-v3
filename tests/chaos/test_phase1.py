"""Chaos suite for the Sovereign Runtime (phase 1).

Adversarial tests for the event bus and config loader. Everything is
deterministic (seeded) and sync-only so it runs green under the STRICT
pytest-asyncio config with no event loop involved.

What this suite pins down as CONTRACT (documented behavior, not bugs):

1. REENTRANCY: emitting from inside a handler must never deadlock. The
   event bus uses threading.RLock, so emit-inside-handler is safe.
2. HANDLER-RAISE: a raising handler propagates to the emitter and skips the
   remaining handlers for that event; the bus stays usable.
3. LOCK HELD DURING HANDLERS: emit() holds the bus lock while calling
   handlers, so a slow handler blocks other emitters. This is a measured
   liveness property and known scaling constraint.
4. CONFIG: get() re-reads the YAML + env on every call. The perf probe
   documents the cost so a fix can be justified.

Failure in any of these = a regression to investigate, not a flake.
"""

from __future__ import annotations

import random
import threading
import time
from typing import Any, Callable, Dict

from msb_v3.core.container import get_container
from msb_v3.core.event_bus import Event, EventBus
from msb_v3.core.runtime_config import get

identity = get_container().identity


# All deadlock-pinning tests run the risky call in a worker thread and
# join(timeout) -- the repo has NO per-test timeout (no pytest-timeout, no
# faulthandler_timeout in pyproject), so a regression to a non-reentrant
# threading.Lock would otherwise HANG pytest instead of failing it. This
# helper converts a would-be hang into a fast, explicit failure.
def run_with_deadlock_guard(fn: Callable[[], Any], timeout: float = 5.0) -> Any:
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


# ---------------------------------------------------------------------------
# 1. REENTRANCY CONTRACT -- emit from inside a handler must never deadlock
# ---------------------------------------------------------------------------


def test_emit_from_within_handler_completes():
    """The deadlock shape: handler emits back onto the same bus."""

    bus = EventBus()
    nested_done = []

    def outer(event: Event) -> None:
        # Emit a second event while the bus lock is held by this handler.
        bus.emit("nested", {"from": "handler"})

    def nested(event: Event) -> None:
        nested_done.append(event)

    bus.subscribe("outer", outer)
    bus.subscribe("nested", nested)

    run_with_deadlock_guard(lambda: bus.emit("outer", {}))

    assert len(nested_done) == 1
    assert nested_done[0].type == "nested"


def test_deep_emit_chain_terminates():
    """Nested emit chains (handler emits, whose handler emits, ...) must
    terminate. The chain is RECURSIVE -- each handler emits the next event
    while still inside the outer emit's handler loop -- so the call stack
    grows to the chain length. The bus must not deadlock or blow up."""

    bus = EventBus()
    depth = [0]
    max_depth = [0]
    hops = []

    def make_handler(tag: str, next_tag: str | None):
        def handler(event: Event) -> None:
            depth[0] += 1
            max_depth[0] = max(max_depth[0], depth[0])
            hops.append(tag)
            if next_tag:
                bus.emit(next_tag, {})
            depth[0] -= 1

        return handler

    # Build a chain of 25 events, each handler emitting the next.
    tags = [f"e{i}" for i in range(25)]
    for i, tag in enumerate(tags):
        bus.subscribe(
            tag, make_handler(tag, tags[i + 1] if i + 1 < len(tags) else None)
        )

    run_with_deadlock_guard(lambda: bus.emit(tags[0], {}))

    assert hops == tags
    # Recursive chain: the innermost emit is 25 frames deep, then unwinds.
    assert max_depth[0] == 25


# ---------------------------------------------------------------------------
# 2. HANDLER FAILURE MODES -- what happens when a subscriber misbehaves
# ---------------------------------------------------------------------------


def test_raising_handler_propagates_and_skips_remaining():
    """A raising handler aborts delivery for the remaining handlers of that
    event but must not corrupt the bus."""

    bus = EventBus()
    calls = []

    def boom(event: Event) -> None:
        calls.append("boom")
        raise RuntimeError("subscriber blew up")

    def after(event: Event) -> None:
        calls.append("after")

    bus.subscribe("x", boom)
    bus.subscribe("x", after)

    raised = False
    try:
        bus.emit("x", {})
    except RuntimeError:
        raised = True

    assert raised
    assert calls == ["boom"]  # 'after' never ran
    assert len(bus.history()) == 1  # event still recorded

    # Bus still usable after the failure.
    bus.emit("y", {})
    assert len(bus.history()) == 2


def test_unsubscribed_during_emit_still_in_snapshot():
    """emit() iterates a snapshot, so a handler that unsubscribes itself
    still receives the in-flight event, and later events stop."""

    bus = EventBus()
    calls = []

    def self_removing(event: Event) -> None:
        calls.append(event.type)
        bus.unsubscribe("x", self_removing)

    bus.subscribe("x", self_removing)
    bus.emit("x", {})
    bus.emit("x", {})

    assert calls == ["x"]  # snapshot delivered once; second emit found no subscriber


def test_clear_is_a_full_reset_including_subscribers():
    """Pinned edge: clear() wipes BOTH history and subscribers.

    This is the current implementation contract (EventBus.clear clears
    _subscribers as well as _history). Any caller that clears the bus for
    a fresh history silently loses all wiring -- a sharp edge worth
    documenting, and a candidate for a history-only reset in the future.
    """
    bus = EventBus()
    seen = []
    bus.subscribe("x", lambda e: seen.append(e))
    bus.emit("x", {})
    bus.clear()
    bus.emit("x", {})
    assert len(bus.history()) == 1  # history restarted from the clear
    assert len(seen) == 1  # subscriber was wiped by clear() -- no delivery
    # Re-subscribing works; the bus is healthy.
    bus.subscribe("x", lambda e: seen.append(e))
    bus.emit("x", {})
    assert len(seen) == 2


# ---------------------------------------------------------------------------
# 3. LIVENESS -- the bus lock is held while handlers run
# ---------------------------------------------------------------------------


def test_slow_handler_blocks_other_emitters():
    """emit() holds the lock during handlers, so a slow handler delays any
    concurrent emitter by (approximately) its runtime. Documented scaling
    constraint, measured here so it cannot silently get worse."""

    bus = EventBus()
    slow_started = threading.Event()

    def slow(event: Event) -> None:
        slow_started.set()
        time.sleep(0.3)

    bus.subscribe("slow", slow)

    # Thread A: runs the slow handler (holds the bus lock for 0.3s).
    slow_runner = threading.Thread(target=lambda: bus.emit("slow", {}))
    slow_runner.start()
    assert slow_started.wait(2.0), "slow handler never started"

    # While A sleeps inside the handler, the lock is held: our own emit of
    # a different event must block until the slow handler finishes.
    t0 = time.perf_counter()
    bus.emit("other", {})
    elapsed = time.perf_counter() - t0

    slow_runner.join(2.0)
    assert not slow_runner.is_alive()
    assert elapsed >= 0.25, f"expected ~0.3s block, got {elapsed:.3f}s"


# ---------------------------------------------------------------------------
# 4. CONCURRENCY STRESS -- many threads, one bus
# ---------------------------------------------------------------------------


def test_concurrent_emits_lose_no_events():
    """8 threads x 250 emits each to one counter subscriber: no lost or
    duplicated events under RLock."""

    bus = EventBus()
    delivered = []
    bus.subscribe("n", lambda e: delivered.append(e))

    def worker(count: int) -> None:
        for i in range(count):
            bus.emit("n", {"i": i})

    threads = [threading.Thread(target=worker, args=(250,)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
        assert not t.is_alive(), "worker thread hung"

    assert len(delivered) == 8 * 250
    assert len(bus.history()) == 8 * 250


def test_concurrent_subscribe_emit_unsubscribe_no_crash():
    """Randomized concurrent bus mutations: no crash, and no deadlock."""

    bus = EventBus()
    errors = []

    def mutator(seed: int) -> None:
        r = random.Random(seed)
        try:
            for _ in range(300):
                op = r.choice(["subscribe", "emit", "unsubscribe", "history", "clear"])
                if op == "subscribe":
                    bus.subscribe(f"t{r.randint(0, 5)}", lambda e: None)
                elif op == "emit":
                    bus.emit(f"t{r.randint(0, 5)}", {})
                elif op == "unsubscribe":
                    # Unsubscribe a real handler: (re)subscribe a fresh one
                    # under a unique type, then remove it.
                    tag = f"u{r.randint(0, 5)}"
                    handler = lambda e: None  # noqa: E731 -- fuzz target
                    bus.subscribe(tag, handler)
                    bus.unsubscribe(tag, handler)
                elif op == "history":
                    bus.history()
                else:
                    bus.clear()
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=mutator, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
        assert not t.is_alive(), "mutator thread hung"

    assert errors == []
    # History is consistent: clear() empties, everything else appends. Just
    # verify we can still use the bus and it is not corrupted.
    bus.emit("final", {})
    assert bus.history()[-1].type == "final"


def test_identity_immutable_and_deterministic():
    # Setting a field on the frozen AgentIdentity raises AttributeError
    # (FrozenInstanceError is a subclass).
    try:
        identity.id = "mutated"  # type: ignore[misc]
        assert False, "identity.id should be frozen"
    except AttributeError:
        pass
    assert identity.id == "sovereign-agent-001"


# ---------------------------------------------------------------------------
# 7. PERF PROBES -- how fast is it, documented so regressions are visible
# ---------------------------------------------------------------------------


def test_perf_emit_throughput():
    """10k emits must complete quickly; prints per-emit cost."""

    bus = EventBus()
    bus.subscribe("x", lambda e: None)
    t0 = time.perf_counter()
    for _ in range(10_000):
        bus.emit("x", {})
    dt = time.perf_counter() - t0
    us = dt / 10_000 * 1_000_000
    print(
        f"\n[perf] emit with 1 subscriber: {us:.1f} us/event ({10_000 / dt:,.0f} events/s)"
    )
    assert dt < 5.0  # generous; regressions from locking changes show here


def test_perf_config_get_reloads_every_call():
    """Documents the dead _config_cache: get() re-reads YAML + env each call.

    This is a measured inefficiency, not a pass/fail gate -- it exists so
    the cost is visible and the fix (actually using _config_cache) can be
    justified by numbers. 2000 gets took ~667ms locally (~334us each).
    """

    n = 500
    t0 = time.perf_counter()
    for _ in range(n):
        get("brain.framework")
    dt = time.perf_counter() - t0
    us = dt / n * 1_000_000
    print(
        f"\n[perf] config get(): {us:.0f} us/call ({n} calls in {dt * 1000:.0f} ms) -- cache is dead code"
    )
    assert dt < 5.0
