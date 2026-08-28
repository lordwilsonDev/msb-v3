"""META-1D: Failure Compiler — structured failure → repair input.

Blueprint §8, §13:
    CODE FAILURE and INTEGRATION FAILURE must become different failure classes.
    Otherwise the system punishes the worker for bugs in the orchestration layer.

    Failure classes:
        MODEL_ERROR, SPEC_ERROR, CONTEXT_ERROR, TOOL_ERROR,
        ENVIRONMENT_ERROR, TEST_ERROR, INTEGRATION_ERROR,
        DEPENDENCY_ERROR, VERIFICATION_ERROR, HARNESS_ERROR

The Failure Compiler classifies failures, diagnoses root causes, and
generates structured repair tasks.  It answers: *Who is actually at fault?*
before retrying anything.
"""

from msb_v3.meta.failure.classifier import FailureClass, FailureClassifier
from msb_v3.meta.failure.escalation import EscalationPolicy
from msb_v3.meta.failure.repair import RepairPolicy

__all__ = [
    "EscalationPolicy",
    "FailureClass",
    "FailureClassifier",
    "RepairPolicy",
]
