"""MSB-v3 Meta-System — the project compiler above the execution kernel.

META-0 (this phase): contracts only. `MetaTask`, `MSL`, `TaskState`,
`ProjectState`, `VerificationResult`, `FailureRecord`, `WorkerResult` — the
model-independent types the compiler, translators, scheduler, verifier, and
failure compiler will share. No orchestration logic lives here yet
(blueprint §21: do not build all of it at once).

Invariants (blueprint M1-M15): the project plan, tasks, and MSL are
model-independent; models receive translated tasks, never raw project
intent; models never define completion — verification does; failures become
structured data that can generate repair tasks; tasks may recursively
decompose; project memory lives outside the model context.
"""

from __future__ import annotations

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

__all__ = [
    "MSL",
    "MSL_VERSION",
    "CheckResult",
    "Complexity",
    "FailureRecord",
    "MetaTask",
    "ProjectState",
    "TaskState",
    "Verdict",
    "VerificationResult",
    "WorkerResult",
    "WorkerStatus",
]
