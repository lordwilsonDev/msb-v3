"""META-0 contract types for the MSB-v3 Meta-System.

Model-independent by construction (blueprint M1-M4, M12): nothing here names
a concrete model or provider. Shapes are informed by three references —

* the Meta-System blueprint (§5 project state machine, §6 task graph,
  §7 Model Task Language, §13 failure compiler, §14 recursive decomposition,
  §15 difficulty routing),
* the Agent Platform Eval Flywheel dataset/metric schema (``EvalCase`` /
  ``AgentData`` trajectory + the metric registry: ``multi_turn_task_success``,
  ``multi_turn_trajectory_quality``, ``multi_turn_tool_use_quality``,
  ``final_response_match`` …) — mirrored by ``VerificationResult.metrics`` and
  ``FailureRecord.cluster_id``,
* the repo's own ``tasks.models.UnifiedTask`` and the ``speech.audio``
  ``RenderResult`` pattern (a status enum + a structured, auditable result
  with ``error_class`` + ``diagnostics``).

No orchestration logic lives in this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

MSL_VERSION = "v1"


def _now() -> str:
    """UTC ISO-8601 timestamp (matches ``tasks.models._now``)."""
    return datetime.now(timezone.utc).isoformat()


class TaskState(str, Enum):
    """Scheduler-visible state of a single ``MetaTask`` (blueprint §6).

    A worker only ever receives a task in ``READY``. ``ESCALATED`` is the
    explicit human boundary (M14) — the loop never silently continues past it.
    """

    READY = "READY"
    BLOCKED = "BLOCKED"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    PASSED = "PASSED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    ESCALATED = "ESCALATED"


class ProjectState(str, Enum):
    """Lifecycle of a whole compiled project (blueprint §5).

    Forward on success; failure can move backwards (VERIFICATION -> DIAGNOSIS
    is modelled as a return to EXECUTION; repeated implementation failure is a
    return to PLANNING / DECOMPOSITION).
    """

    INTAKE = "INTAKE"
    DISCOVERY = "DISCOVERY"
    UNDERSTANDING = "UNDERSTANDING"
    ARCHITECTURE = "ARCHITECTURE"
    PLANNING = "PLANNING"
    DECOMPOSITION = "DECOMPOSITION"
    EXECUTION = "EXECUTION"
    VERIFICATION = "VERIFICATION"
    INTEGRATION = "INTEGRATION"
    SYSTEM_TEST = "SYSTEM_TEST"
    RELEASE = "RELEASE"


class Complexity(str, Enum):
    """Difficulty-estimator output that drives model routing (blueprint §15).

    The router selects a worker tier from this, never the reverse: a model is
    chosen because the task requires it (M12).
    """

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class WorkerStatus(str, Enum):
    """What a worker execution produced, before verification runs."""

    PRODUCED = "PRODUCED"      # an artifact/change was produced
    NO_CHANGE = "NO_CHANGE"    # ran cleanly but produced nothing
    ERROR = "ERROR"            # the worker itself failed (crash, timeout, refusal)


class Verdict(str, Enum):
    """Terminal verdict of a verification pass.

    Shares the vocabulary of ``speech.audio.RenderStatus`` and
    ``docs/governance/attack-matrix-*.md``: an absent dependency is an explicit
    ``EXPECTED_SKIP``, never a silent pass. Completion is defined here, never
    by the worker (M5, M6).
    """

    PASS = "PASS"
    FAIL = "FAIL"
    EXPECTED_SKIP = "EXPECTED_SKIP"


@dataclass
class MetaTask:
    """A unit of project work, model-independent (blueprint §6).

    Lives in the task graph; ``dependencies`` are other ``task_id`` values.
    ``parent_id`` / ``children`` carry recursive decomposition (§14): a task
    too hard for the available worker capacity is split until
    ``complexity`` drops. ``assigned_model`` is a scheduling annotation only —
    clearing or changing it never rewrites the task (M12).
    """

    task_id: str
    objective: str
    task_type: str = "implementation"          # implementation | analysis | repair | test | doc
    priority: str = "P2"                        # P0 | P1 | P2 | P3
    state: TaskState = TaskState.BLOCKED

    # Context the context-compiler will resolve into a token budget (§9).
    architecture_refs: List[str] = field(default_factory=list)
    relevant_files: List[str] = field(default_factory=list)
    relevant_tests: List[str] = field(default_factory=list)

    dependencies: List[str] = field(default_factory=list)   # prerequisite task_ids
    parent_id: Optional[str] = None
    children: List[str] = field(default_factory=list)

    complexity: Optional[Complexity] = None
    assigned_model: Optional[str] = None       # annotation only; not part of identity

    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class MSL:
    """Model Task Language — the translated, model-independent execution
    representation a per-model Translator consumes to build a prompt
    (blueprint §7). ``PROJECT TASK != MODEL PROMPT``: this survives model
    replacement (M3, M12).

    Versioned (``msl_version``) so a breaking change is v1 -> v2, never a
    silent shape change (blueprint §15).
    """

    msl_id: str
    source_task_id: str
    objective: str
    task_type: str = "implementation"
    priority: str = "P2"
    msl_version: str = MSL_VERSION

    architecture_refs: List[str] = field(default_factory=list)
    relevant_files: List[str] = field(default_factory=list)
    relevant_tests: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)

    allowed_actions: List[str] = field(default_factory=list)     # e.g. read, write, test
    forbidden_actions: List[str] = field(default_factory=list)   # e.g. network_access, dependency_install
    constraints: Dict[str, Any] = field(default_factory=dict)    # max_files_changed, preserve_public_api, preserve_governance …

    verification_commands: List[str] = field(default_factory=list)
    success_criteria: Dict[str, bool] = field(default_factory=dict)  # tests_pass, lint_pass, architecture_contract …
    max_attempts: int = 3
    escalation: str = "replan"                                   # replan | redecompose | human

    created_at: str = field(default_factory=_now)


@dataclass
class WorkerResult:
    """What a worker returned for one attempt at one task, before verification.

    Raw outcome only — ``status`` says whether an artifact exists, not whether
    it is correct (that is ``VerificationResult``).
    """

    task_id: str
    worker_id: str                              # opaque worker/model identifier
    status: WorkerStatus
    attempt: int = 1

    artifact_ref: str = ""                      # patch / diff / worktree path
    tool_events: List[Dict[str, Any]] = field(default_factory=list)
    stdout_ref: str = ""
    stderr_ref: str = ""

    tokens_in: int = 0
    tokens_out: int = 0
    duration_ms: float = 0.0

    error_class: str = ""                       # set when status is ERROR
    message: str = ""
    started_at: str = field(default_factory=_now)


@dataclass
class CheckResult:
    """One machine-run check inside a verification pass."""

    name: str                                   # e.g. "pytest tests/providers/", "ruff", "import-direction"
    passed: bool
    detail: str = ""
    evidence_ref: str = ""                      # sha256 / path of captured output


@dataclass
class VerificationResult:
    """The verifier's structured verdict on a ``WorkerResult`` (blueprint §12).

    ``metrics`` is keyed by Eval-Flywheel-style metric ids
    (``multi_turn_task_success``, ``multi_turn_tool_use_quality``,
    ``final_response_match`` …) so the benchmark harness (§17-18) and the
    factory reviewer share one vocabulary. Completion is decided here.
    """

    task_id: str
    verdict: Verdict
    checks: List[CheckResult] = field(default_factory=list)
    metrics: Dict[str, float] = field(default_factory=dict)
    evidence: List[str] = field(default_factory=list)     # sha256 / path refs, replayable
    worker_id: str = ""
    attempt: int = 1
    message: str = ""
    completed_at: str = field(default_factory=_now)

    @property
    def all_required_passed(self) -> bool:
        """``ALL_REQUIRED_CHECKS == PASS`` gate (blueprint §12)."""
        return self.verdict is Verdict.PASS and all(c.passed for c in self.checks)


@dataclass
class FailureRecord:
    """Structured failure -> repair input (blueprint §13).

    The Failure Compiler turns a ``FAIL`` ``VerificationResult`` into one of
    these; a repair ``MetaTask`` is generated from it (M7, M8). ``cluster_id``
    supports Eval-Flywheel-style loss clustering (§16) so recurring failures
    are grouped rather than re-diagnosed each time.
    """

    failure_id: str
    task_id: str
    symptom: str
    evidence: List[str] = field(default_factory=list)         # path:line / sha refs
    likely_causes: List[str] = field(default_factory=list)
    recommended_action: str = ""
    repair_scope: List[str] = field(default_factory=list)     # paths a repair task may touch
    retry_allowed: bool = True
    cluster_id: Optional[str] = None
    source_verification_task_id: str = ""
    created_at: str = field(default_factory=_now)
