"""META-1: Execution Policy — the missing layer between task and worker.

The ExecutionPolicy is the output of the Intelligence Router (blueprint §META-1).
It selects the *shape of cognition* before any worker runs:

    Task characteristics
            ↓
    Complexity Analysis
            ↓
    ExecutionPolicy
            ↓
    Worker selection
            ↓
    Context policy
            ↓
    Verification policy
            ↓
    Execution

A policy specifies:
    - execution mode (Fable / Hybrid / Local)
    - planner model
    - executor model
    - verifier model
    - context strategy (FULL / COMPILED / MINIMAL)
    - tool strategy (FULL / LIMITED / TASK_SPEC)
    - reasoning strategy (OPEN / STRUCTURED / DECOMPOSED)
    - verification strategy (STANDARD / STRICT / FUZZY)
    - parallelism
    - max retries
    - escalation target

Architecture law:
    Same META system.  Completely different execution strategy.
    The model is a replaceable component inside the execution shape.
"""

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

__all__ = [
    "ContextStrategy",
    "ExecutionMode",
    "ExecutionPolicy",
    "ExecutionShape",
    "PolicyRouter",
    "PolicyScores",
    "ReasoningStrategy",
    "ToolStrategy",
    "VerificationStrategy",
]
