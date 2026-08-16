"""Phase 0 test suite.

Completion criteria:
- `pytest tests/` passes
- Event bus emits and delivers events
- Identity is deterministic

(M3 convergence: the config-loader and HealthSystem tests were removed with
`core/runtime_config.py` and `core/health.py` — both were test-only dead
code with zero runtime callers; the live health path is `api/health.py` +
`api/system.py /system/health`, covered by `tests/api/test_system_health.py`.)
"""

from __future__ import annotations

import time

from msb_v3 import __version__
from msb_v3.core.container import get_container
from msb_v3.core.event_bus import Event, EventBus

identity = get_container().identity


def test_event_bus_emit_and_deliver():
    bus = EventBus()
    received = []

    def handler(event: Event) -> None:
        received.append(event)

    bus.subscribe("agent.goal.received", handler)
    event = bus.emit("agent.goal.received", {"goal": "test"})
    assert event.type == "agent.goal.received"
    assert event.payload == {"goal": "test"}
    assert len(received) == 1


def test_event_bus_history():
    bus = EventBus()
    bus.emit("a", {"x": 1})
    bus.emit("b", {"y": 2})
    history = bus.history()
    assert len(history) == 2
    assert bus.history("a")[0].type == "a"


def test_event_bus_unsubscribe():
    bus = EventBus()
    calls = []

    def handler(event: Event) -> None:
        calls.append(event)

    bus.subscribe("x", handler)
    bus.emit("x", {})
    bus.unsubscribe("x", handler)
    bus.emit("x", {})
    assert len(calls) == 1


def test_event_has_required_fields():
    bus = EventBus()
    event = bus.emit("test", {})
    assert event.event_id
    assert event.trace_id
    assert event.timestamp <= time.time()
    assert event.agent_id == identity.id


def test_identity_deterministic():
    assert identity.id == "sovereign-agent-001"
    assert identity.version == __version__
    assert identity.runtime == "msb-v3"


