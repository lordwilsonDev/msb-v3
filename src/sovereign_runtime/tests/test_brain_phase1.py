from __future__ import annotations

from typing import Any

import pytest

from sovereign_runtime.brain import BrainService
from sovereign_runtime.core.identity import identity
from sovereign_runtime.events.event_bus import Event, EventBus


def make_brain(bus: EventBus | None = None) -> BrainService:
    return BrainService(bus=bus or EventBus())


def test_brain_ignores_empty_goal():
    bus = EventBus()
    brain = make_brain(bus)
    brain._on_goal_received(Event(type="agent.goal.received", payload={"goal": "   "}, timestamp="", agent_id="", trace_id=""))
    assert "agent.plan.created" not in bus._history  # noqa: SLF001


def test_brain_creates_plan_on_goal():
    bus = EventBus()
    brain = make_brain(bus)
    brain._on_goal_received(Event(type="agent.goal.received", payload={"goal": "Build a website"}, timestamp="", agent_id="", trace_id=""))
    plan_events = [event for event in bus._history if event.type == "agent.plan.created"]  # noqa: SLF001
    assert len(plan_events) == 1
    plan = plan_events[0].payload["plan"]
    assert plan["goal"] == "Build a website"
    assert plan["depth"] == 0


def test_brain_plan_step_fields():
    bus = EventBus()
    brain = make_brain(bus)
    brain._on_goal_received(Event(type="agent.goal.received", payload={"goal": "x" * 200}, timestamp="", agent_id="", trace_id=""))
    plan_events = [event for event in bus._history if event.type == "agent.plan.created"]  # noqa: SLF001
    assert len(plan_events) == 1
    plan = plan_events[0].payload["plan"]
    assert plan["status"] in {"ready", "terminated"}


def test_brain_health_online():
    brain = make_brain()
    assert brain.health()["status"] == "online"


def test_brain_plan_includes_created_by():
    bus = EventBus()
    brain = make_brain(bus)
    brain._on_goal_received(Event(type="agent.goal.received", payload={"goal": "hello"}, timestamp="", agent_id="", trace_id=""))
    plan_events = [event for event in bus._history if event.type == "agent.plan.created"]  # noqa: SLF001
    assert plan_events[0].payload["created_by"] == identity.id


def test_brain_emits_execute_request():
    bus = EventBus()
    brain = make_brain(bus)
    brain._on_goal_received(Event(type="agent.goal.received", payload={"goal": "hello"}, timestamp="", agent_id="", trace_id=""))
    exec_events = [event for event in bus._history if event.type == "agent.execute.request"]  # noqa: SLF001
    assert len(exec_events) == 1
    assert "plan" in exec_events[0].payload
