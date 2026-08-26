"""Smoke tests for guardrails/fold.py — StepTracker, StepEnforcer, RespondTool.

Guardrails enforce that required steps complete before terminal actions execute.
This is a safety boundary used by DeepSeek, Anthropic, and Ollama providers.
"""
from __future__ import annotations

from msb_v3.guardrails.fold import Nudge, RespondTool, StepEnforcer, StepTracker

# ---------------------------------------------------------------------------
# StepTracker
# ---------------------------------------------------------------------------

class TestStepTracker:
    def test_empty_tracker_not_satisfied(self):
        tracker = StepTracker(required_steps=["search", "verify"])
        assert not tracker.is_satisfied()
        assert tracker.pending() == ["search", "verify"]

    def test_record_step(self):
        tracker = StepTracker(required_steps=["search", "verify"])
        tracker.record("search")
        assert not tracker.is_satisfied()
        assert tracker.pending() == ["verify"]

    def test_all_steps_satisfied(self):
        tracker = StepTracker(required_steps=["search", "verify"])
        tracker.record("search")
        tracker.record("verify")
        assert tracker.is_satisfied()
        assert tracker.pending() == []

    def test_extra_steps_dont_break(self):
        tracker = StepTracker(required_steps=["search"])
        tracker.record("search")
        tracker.record("unrelated_tool")
        assert tracker.is_satisfied()

    def test_summary_hint(self):
        tracker = StepTracker(required_steps=["a", "b"])
        assert "No steps completed" in tracker.summary_hint()
        tracker.record("a")
        assert "a" in tracker.summary_hint()


# ---------------------------------------------------------------------------
# StepEnforcer
# ---------------------------------------------------------------------------

class TestStepEnforcer:
    def test_no_terminal_tool_returns_none(self):
        enforcer = StepEnforcer(
            required_steps=["search"],
            terminal_tools=frozenset({"respond"}),
        )
        result = enforcer.check([{"tool": "search", "query": "test"}])
        assert result is None

    def test_terminal_before_required_returns_nudge(self):
        enforcer = StepEnforcer(
            required_steps=["search", "verify"],
            terminal_tools=frozenset({"respond"}),
        )
        result = enforcer.check([{"tool": "respond", "answer": "done"}])
        assert result is not None
        assert isinstance(result, Nudge)
        assert result.kind == "step"
        assert "search" in result.content or "verify" in result.content

    def test_terminal_after_required_returns_none(self):
        enforcer = StepEnforcer(
            required_steps=["search"],
            terminal_tools=frozenset({"respond"}),
        )
        enforcer.record("search")
        result = enforcer.check([{"tool": "respond", "answer": "done"}])
        assert result is None

    def test_premature_attempts_increase_tier(self):
        enforcer = StepEnforcer(
            required_steps=["search"],
            terminal_tools=frozenset({"respond"}),
        )
        r1 = enforcer.check([{"tool": "respond"}])
        r2 = enforcer.check([{"tool": "respond"}])
        r3 = enforcer.check([{"tool": "respond"}])
        assert r1.tier < r2.tier < r3.tier

    def test_tier_caps_at_3(self):
        enforcer = StepEnforcer(
            required_steps=["search"],
            terminal_tools=frozenset({"respond"}),
        )
        for _ in range(10):
            enforcer.check([{"tool": "respond"}])
        # After enough attempts, tier should cap
        result = enforcer.check([{"tool": "respond"}])
        assert result.tier <= 3


# ---------------------------------------------------------------------------
# RespondTool
# ---------------------------------------------------------------------------

class TestRespondTool:
    def test_spec_has_required_fields(self):
        spec = RespondTool.spec()
        assert spec["type"] == "function"
        assert spec["name"] == "respond"
        assert "description" in spec

    def test_spec_has_parameters(self):
        spec = RespondTool.spec()
        params = spec.get("parameters", {})
        assert "properties" in params or "type" in params
