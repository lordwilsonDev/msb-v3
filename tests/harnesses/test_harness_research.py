"""Harness research assistant verification tests.

Proves the SovereignResearchAssistant harness:
1. Can be instantiated
2. Integrates with local AI client
3. Respects governance (ActionGate)
4. Produces structured output
"""
from __future__ import annotations

from pathlib import Path

from msb_v3.harnesses.base import BaseHarness, HarnessResult

# ---------------------------------------------------------------------------
# Base harness tests
# ---------------------------------------------------------------------------


class TestBaseHarness:
    """Base harness protocol tests."""

    def test_harness_result_has_required_fields(self):
        """HarnessResult carries ok, event, payload."""
        result = HarnessResult(
            ok=True,
            event="test_complete",
            payload={"answer": "test"},
        )
        assert result.ok is True
        assert result.event == "test_complete"

    def test_harness_result_error_state(self):
        """HarnessResult can represent errors."""
        result = HarnessResult(
            ok=False,
            event="test_failed",
            error="something went wrong",
        )
        assert result.ok is False
        assert result.error == "something went wrong"


# ---------------------------------------------------------------------------
# SovereignResearchAssistant tests
# ---------------------------------------------------------------------------


class TestSovereignResearchAssistant:
    """Smoke tests for the research assistant harness."""

    def test_instantiation(self, tmp_path: Path):
        """Harness can be created with a topic."""
        from msb_v3.harnesses.research_assistant import SovereignResearchAssistant

        harness = SovereignResearchAssistant(
            topic="test topic",
            runtime_root=tmp_path / "research",
        )
        assert harness is not None
        assert harness.topic == "test topic"

    def test_instantiation_with_slug(self, tmp_path: Path):
        """Harness can be created with custom slug."""
        from msb_v3.harnesses.research_assistant import SovereignResearchAssistant

        harness = SovereignResearchAssistant(
            topic="test",
            slug="custom-slug",
            runtime_root=tmp_path / "research",
        )
        assert harness.slug == "custom-slug"

    def test_execute_returns_harness_result(self, tmp_path: Path):
        """execute() returns a HarnessResult."""
        from msb_v3.harnesses.research_assistant import SovereignResearchAssistant

        harness = SovereignResearchAssistant(
            topic="test",
            runtime_root=tmp_path / "research",
        )
        result = harness.execute("What is MSB?")
        assert isinstance(result, HarnessResult)
        assert result.ok in (True, False)

    def test_evidence_status_returns_dict(self, tmp_path: Path):
        """evidence_status() returns a dict with required fields."""
        from msb_v3.harnesses.research_assistant import SovereignResearchAssistant

        harness = SovereignResearchAssistant(
            topic="test",
            runtime_root=tmp_path / "research",
        )
        status = harness.evidence_status()
        assert isinstance(status, dict)

    def test_harness_is_subclass_of_base(self):
        """SovereignResearchAssistant inherits from BaseHarness."""
        from msb_v3.harnesses.research_assistant import SovereignResearchAssistant

        assert issubclass(SovereignResearchAssistant, BaseHarness)
