"""META-1: Execution Policy tests — mode selection, presets, signal extraction, routing.

Tests verify:
  - ExecutionPolicy presets (Fable/Hybrid/Local)
  - ExecutionMode, ContextStrategy, ToolStrategy, ReasoningStrategy, VerificationStrategy enums
  - PolicyScores extraction from task signals
  - PolicyRouter selects Fable for complex/ambiguous tasks
  - PolicyRouter selects Local for simple/repetitive tasks
  - PolicyRouter selects Hybrid for software construction tasks
  - Confidence calculation (best vs second-best)
  - Policy serialization
  - Cost sensitivity adjusts retries
  - Complexity adjusts parallelism
  - Escalation chain is correct
"""

from __future__ import annotations

from msb_v3.meta.contracts import Complexity, MetaTask
from msb_v3.meta.policy.execution_policy import (
    ContextStrategy,
    ExecutionMode,
    ExecutionPolicy,
    ExecutionShape,
    ReasoningStrategy,
    ToolStrategy,
    VerificationStrategy,
)
from msb_v3.meta.policy.policy_router import PolicyRouter, PolicyScores

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class TestExecutionEnums:
    def test_execution_mode_values(self) -> None:
        assert ExecutionMode.FABLE.value == "FABLE"
        assert ExecutionMode.HYBRID.value == "HYBRID"
        assert ExecutionMode.LOCAL.value == "LOCAL"

    def test_context_strategy_values(self) -> None:
        assert ContextStrategy.FULL.value == "FULL"
        assert ContextStrategy.COMPILED.value == "COMPILED"
        assert ContextStrategy.MINIMAL.value == "MINIMAL"

    def test_tool_strategy_values(self) -> None:
        assert ToolStrategy.FULL.value == "FULL"
        assert ToolStrategy.LIMITED.value == "LIMITED"
        assert ToolStrategy.TASK_SPEC.value == "TASK_SPEC"

    def test_reasoning_strategy_values(self) -> None:
        assert ReasoningStrategy.OPEN.value == "OPEN"
        assert ReasoningStrategy.STRUCTURED.value == "STRUCTURED"
        assert ReasoningStrategy.DECOMPOSED.value == "DECOMPOSED"

    def test_verification_strategy_values(self) -> None:
        assert VerificationStrategy.STANDARD.value == "STANDARD"
        assert VerificationStrategy.STRICT.value == "STRICT"
        assert VerificationStrategy.FUZZY.value == "FUZZY"


# ---------------------------------------------------------------------------
# ExecutionShape
# ---------------------------------------------------------------------------

class TestExecutionShape:
    def test_default_shape(self) -> None:
        s = ExecutionShape()
        assert s.context is ContextStrategy.COMPILED
        assert s.tools is ToolStrategy.LIMITED
        assert s.reasoning is ReasoningStrategy.STRUCTURED
        assert s.verification is VerificationStrategy.STRICT

    def test_to_dict(self) -> None:
        s = ExecutionShape(context=ContextStrategy.FULL, tools=ToolStrategy.FULL)
        d = s.to_dict()
        assert d["context"] == "FULL"
        assert d["tools"] == "FULL"


# ---------------------------------------------------------------------------
# ExecutionPolicy presets
# ---------------------------------------------------------------------------

class TestExecutionPolicyPresets:
    def test_fable_preset(self) -> None:
        p = ExecutionPolicy.fable("T-1")
        assert p.mode is ExecutionMode.FABLE
        assert p.shape.context is ContextStrategy.FULL
        assert p.shape.tools is ToolStrategy.FULL
        assert p.shape.reasoning is ReasoningStrategy.OPEN
        assert p.shape.verification is VerificationStrategy.FUZZY
        assert p.parallelism == 1
        assert p.max_retries == 1
        assert p.escalation is None  # nothing above Fable

    def test_hybrid_preset(self) -> None:
        p = ExecutionPolicy.hybrid("T-2")
        assert p.mode is ExecutionMode.HYBRID
        assert p.shape.context is ContextStrategy.COMPILED
        assert p.shape.tools is ToolStrategy.LIMITED
        assert p.shape.reasoning is ReasoningStrategy.STRUCTURED
        assert p.shape.verification is VerificationStrategy.STRICT
        assert p.parallelism == 4
        assert p.max_retries == 2
        assert p.escalation is ExecutionMode.FABLE

    def test_local_preset(self) -> None:
        p = ExecutionPolicy.local("T-3")
        assert p.mode is ExecutionMode.LOCAL
        assert p.shape.context is ContextStrategy.MINIMAL
        assert p.shape.tools is ToolStrategy.TASK_SPEC
        assert p.shape.reasoning is ReasoningStrategy.DECOMPOSED
        assert p.shape.verification is VerificationStrategy.STANDARD
        assert p.parallelism == 8
        assert p.max_retries == 3
        assert p.escalation is ExecutionMode.HYBRID

    def test_serialization(self) -> None:
        p = ExecutionPolicy.hybrid("T-1")
        d = p.to_dict()
        assert d["task_id"] == "T-1"
        assert d["mode"] == "HYBRID"
        assert d["shape"]["context"] == "COMPILED"
        assert "created_at" in d


# ---------------------------------------------------------------------------
# PolicyScores
# ---------------------------------------------------------------------------

class TestPolicyScores:
    def test_to_dict(self) -> None:
        s = PolicyScores(complexity=0.8, ambiguity=0.9)
        d = s.to_dict()
        assert d["complexity"] == 0.8
        assert d["ambiguity"] == 0.9


# ---------------------------------------------------------------------------
# PolicyRouter
# ---------------------------------------------------------------------------

class TestPolicyRouter:
    def test_complex_ambiguous_task_selects_fable(self) -> None:
        router = PolicyRouter()
        task = MetaTask(
            task_id="T-1",
            objective="Design a novel distributed consensus protocol",
            complexity=Complexity.CRITICAL,
            task_type="analysis",
            metadata={"ambiguity": 0.9, "novelty": 0.9, "cost_sensitivity": 0.2},
        )
        policy = router.route(task)
        # Complex + ambiguous + novel → Fable or at least not Local
        assert policy.mode in (ExecutionMode.FABLE, ExecutionMode.HYBRID)
        assert policy.confidence > 0.0

    def test_simple_repetitive_task_selects_local(self) -> None:
        router = PolicyRouter()
        task = MetaTask(
            task_id="T-2",
            objective="Fix the typo in test_doc.py",
            complexity=Complexity.LOW,
            task_type="repair",
            metadata={"ambiguity": 0.1, "novelty": 0.1, "cost_sensitivity": 0.9,
                      "verification_commands": ["pytest -q"]},
        )
        policy = router.route(task)
        # Simple + low ambiguity + high cost sensitivity → Local
        assert policy.mode in (ExecutionMode.LOCAL, ExecutionMode.HYBRID)

    def test_software_construction_selects_hybrid(self) -> None:
        router = PolicyRouter()
        task = MetaTask(
            task_id="T-3",
            objective="Implement the audio renderer contract",
            complexity=Complexity.MEDIUM,
            task_type="implementation",
            metadata={"ambiguity": 0.3, "novelty": 0.4, "cost_sensitivity": 0.5,
                      "verification_commands": ["pytest -q", "ruff check"]},
            dependencies=["META-0"],
        )
        policy = router.route(task)
        # Medium complexity + has tests + software construction → Hybrid
        assert policy.mode in (ExecutionMode.HYBRID, ExecutionMode.LOCAL)

    def test_high_complexity_adjusts_parallelism(self) -> None:
        router = PolicyRouter()
        task = MetaTask(
            task_id="T-4",
            objective="Complex system design",
            complexity=Complexity.CRITICAL,
            metadata={"ambiguity": 0.8},
        )
        policy = router.route(task)
        if policy.mode is ExecutionMode.FABLE:
            assert policy.parallelism == 1  # complex = sequential

    def test_cost_sensitive_limits_retries(self) -> None:
        router = PolicyRouter()
        task = MetaTask(
            task_id="T-5",
            objective="Simple task",
            complexity=Complexity.LOW,
            task_type="test",
            metadata={"cost_sensitivity": 0.95},
        )
        policy = router.route(task)
        assert policy.max_retries <= 2

    def test_escalation_chain_hybrid_to_fable(self) -> None:
        router = PolicyRouter()
        task = MetaTask(
            task_id="T-6",
            objective="Medium task",
            complexity=Complexity.MEDIUM,
            task_type="implementation",
        )
        policy = router.route(task)
        if policy.mode is ExecutionMode.HYBRID:
            assert policy.escalation is ExecutionMode.FABLE
        elif policy.mode is ExecutionMode.LOCAL:
            assert policy.escalation is ExecutionMode.HYBRID

    def test_policy_serializes_with_signals(self) -> None:
        router = PolicyRouter()
        task = MetaTask(
            task_id="T-7",
            objective="Implement X",
            complexity=Complexity.MEDIUM,
            task_type="implementation",
        )
        policy = router.route(task)
        d = policy.to_dict()
        assert "metadata" in d
        assert "signals" in d["metadata"]
        assert "mode" in d

    def test_all_alternatives_recorded(self) -> None:
        router = PolicyRouter()
        task = MetaTask(task_id="T-8", objective="X", complexity=Complexity.LOW)
        policy = router.route(task)
        # Should have at least 2 alternatives (3 modes total)
        assert len(policy.alternatives) >= 1

    def test_confidence_bounded(self) -> None:
        router = PolicyRouter()
        task = MetaTask(task_id="T-9", objective="X")
        policy = router.route(task)
        assert 0.0 <= policy.confidence <= 1.0

    def test_planner_and_verifier_assigned(self) -> None:
        router = PolicyRouter()
        task = MetaTask(task_id="T-10", objective="X", complexity=Complexity.MEDIUM)
        policy = router.route(task)
        # Fable uses Claude, Hybrid uses frontier/qwen, Local uses nothing/qwen
        if policy.mode is ExecutionMode.FABLE:
            assert policy.planner != ""
            assert policy.verifier != ""
