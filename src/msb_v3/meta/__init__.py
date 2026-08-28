"""MSB-v3 Meta-System — the project compiler above the execution kernel.

META-0: contracts only.  META-1A-F: translation, routing, probability,
failure compilation.

Invariants (blueprint M1-M15):
    - The project plan, tasks, and MSL are model-independent.
    - Models receive translated tasks, never raw project intent.
    - Models never define completion — verification does.
    - Failures become structured data that can generate repair tasks.
    - Tasks may recursively decompose.
    - Project memory lives outside the model context.
    - The intelligence is in the system, not locked inside the model.
"""

from __future__ import annotations

# META-0: Contracts
from msb_v3.meta.contracts import (
    MSL,
    MSL_VERSION,
    CheckResult,
    Complexity,
    FailureRecord,
    MetaTask,
    ProjectState,
    TaskState,
    Verdict,
    VerificationResult,
    WorkerResult,
    WorkerStatus,
)

# META-1D: Failure Compiler
from msb_v3.meta.failure import (
    EscalationPolicy,
    FailureClass,
    FailureClassifier,
    RepairPolicy,
)

# META-5: Outcome Ledger
from msb_v3.meta.outcome import OutcomeLedger, PipelineOutcome

# META-1C: Probability Engine
from msb_v3.meta.probability import (
    HistoricalPerformance,
    RoutingMatrix,
    WorkerStats,
)

# META-1B: Router
from msb_v3.meta.routing import (
    CapabilityMatcher,
    MatchResult,
    RegisteredSkill,
    RegisteredWorker,
    RouteDecision,
    SkillBridge,
    SkillRegistry,
    WorkerRegistry,
)

# META-1A: Translation Engine
from msb_v3.meta.translation import (
    ContextBudget,
    ContextCompiler,
    ContextSelection,
    ModelTask,
    TaskTranslator,
    ToolPolicy,
    WorkerProfile,
)

__all__ = [
    # META-0
    "MSL", "MSL_VERSION", "CheckResult", "Complexity", "FailureRecord",
    "MetaTask", "ProjectState", "TaskState", "Verdict", "VerificationResult",
    "WorkerResult", "WorkerStatus",
    # META-1A
    "ContextBudget", "ContextCompiler", "ContextSelection", "ModelTask",
    "TaskTranslator", "ToolPolicy", "WorkerProfile",
    # META-1B
    "CapabilityMatcher", "MatchResult", "RegisteredSkill", "RegisteredWorker",
    "RouteDecision", "SkillBridge", "SkillRegistry", "WorkerRegistry",
    # META-1C
    "HistoricalPerformance", "RoutingMatrix", "WorkerStats",
    # META-1D
    "EscalationPolicy", "FailureClass", "FailureClassifier", "RepairPolicy",
    # META-5
    "OutcomeLedger", "PipelineOutcome",
]
