"""Phase 0 test suite.

Completion criteria:
- `pytest tests/` passes
- Event bus emits and delivers events
- Identity is deterministic
- Config loads with env overrides
- Health system reports components
"""

from __future__ import annotations

import time

import pytest

from msb_v3 import __version__
from msb_v3.core.container import get_container
from msb_v3.core.event_bus import Event, EventBus
from msb_v3.core.health import HealthSystem
from msb_v3.core.runtime_config import get, load_config

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


def test_config_defaults():
    cfg = load_config()
    assert cfg["brain"]["framework"] == "motia"
    assert cfg["safety"]["fail_closed"] is True


def test_config_override(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("SOVEREIGN_SAFETY_FAIL_CLOSED", "false")
    assert get("safety.fail_closed") is False


def test_health_system_all_online():
    hs = HealthSystem(agent_id=identity.id)
    hs.register("bus", lambda: True)
    report = hs.check()
    assert report.overall == "online"
    assert len(report.components) == 1
    assert report.components[0].status == "online"


def test_health_system_degraded():
    hs = HealthSystem(agent_id=identity.id)
    hs.register("bus", lambda: True)
    hs.register("db", lambda: False)
    report = hs.check()
    assert report.overall == "degraded"
    assert report.to_dict()["agent_id"] == identity.id


def test_health_component_error():
    hs = HealthSystem()
    hs.register("broken", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    report = hs.check()
    assert report.components[0].status == "error"
    assert "boom" in report.components[0].detail
