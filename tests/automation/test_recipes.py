"""Tests for the recipe language (automation/recipes.py) — the operator's
own deterministic 'when X then Y' grammar, zero LLM spend."""

from __future__ import annotations

from msb_v3.automation.recipes import parse


def test_every_minutes_recipe() -> None:
    plan = parse("every 30 minutes, post a heartbeat to http://127.0.0.1:5678/webhook/msb-ping")
    assert plan is not None
    assert plan["provider"] == "self"
    assert plan["schedule"] == "*/30 * * * *"
    assert plan["action"]["type"] == "webhook_post"
    assert plan["action"]["url"] == "http://127.0.0.1:5678/webhook/msb-ping"
    assert plan["name"] == "msb-ping"


def test_every_hours_recipe() -> None:
    plan = parse("every 2 hours, ping https://hook.example.com/status")
    assert plan is not None
    assert plan["schedule"] == "0 */2 * * *"


def test_hourly_recipe() -> None:
    plan = parse("hourly, ping https://hook.example.com/tick")
    assert plan is not None
    assert plan["schedule"] == "0 * * * *"


def test_daily_at_recipe() -> None:
    plan = parse("daily at 09:00, ping https://hook.make.com/abc")
    assert plan is not None
    assert plan["schedule"] == "0 9 * * *"


def test_named_recipe() -> None:
    plan = parse("every 5 minutes, post a status to http://127.0.0.1:9/hook/x, named morning-ping")
    assert plan is not None
    assert plan["name"] == "morning-ping"


def test_non_recipe_returns_none() -> None:
    assert parse("") is None
    assert parse("ping me on slack when a lead comes in") is None  # no schedule
    assert parse("every 30 minutes, do something cool") is None  # no url
    assert parse("when a new lead arrives, notify me") is None  # neither
