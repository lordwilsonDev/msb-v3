"""META-4: One-Worker Loop — the full pipeline.

This is the first working end-to-end pipeline that proves the META architecture
composes.  It takes a MetaTask and drives it through:

    MetaTask
        ↓
    PolicyRouter → ExecutionPolicy
        ↓
    TaskTranslator → ModelTask
        ↓
    Worker (injected callable) → WorkerResult
        ↓
    VerificationGate → GateResult
        ↓
    FinalResult (with evidence chain)

Every stage is injectable.  The worker is a `Callable[[str], str]`.
The model is never a hard dependency.

Usage::

    from msb_v3.meta.pipeline import MetaPipeline

    pipeline = MetaPipeline(worker=my_model_call)
    result = pipeline.run(meta_task)

    if result.gate.passed:
        print(f"Verified in {result.policy.mode.value} mode")
    else:
        print(f"Failed: {result.gate.reason}")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from msb_v3.meta.contracts import (
    MetaTask,
    Verdict,
    VerificationResult,
    WorkerResult,
    WorkerStatus,
)
from msb_v3.meta.outcome.ledger import OutcomeLedger, PipelineOutcome
from msb_v3.meta.policy.execution_policy import ExecutionPolicy
from msb_v3.meta.policy.policy_router import PolicyRouter
from msb_v3.meta.routing.worker_registry import WorkerRegistry
from msb_v3.meta.translation.context_compiler import ContextCompiler
from msb_v3.meta.translation.import_graph import ImportGraph
from msb_v3.meta.translation.task_translator import TaskTranslator, WorkerProfile
from msb_v3.meta.verification.gate import GateResult, VerificationGate

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


ModelCall = Callable[[str], str]


@dataclass
class PipelineStage:
    """One stage in the pipeline execution trace."""

    name: str
    started_at: str = field(default_factory=_now)
    completed_at: str = ""
    duration_ms: float = 0.0
    success: bool = True
    output_summary: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FinalResult:
    """The complete output of one pipeline run."""

    task_id: str
    gate: GateResult
    policy: ExecutionPolicy
    stages: List[PipelineStage] = field(default_factory=list)

    # Worker result (for repair pipeline).
    worker_result: Optional[WorkerResult] = None
    verification: Optional[VerificationResult] = None

    # Audit.
    completed_at: str = field(default_factory=_now)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.gate.passed

    @property
    def verdict(self) -> Verdict:
        return self.gate.verdict

    def to_dict(self) -> Dict[str, Any]:
        """Serialize for evidence/audit trail."""
        return {
            "task_id": self.task_id,
            "verdict": self.verdict.value,
            "mode": self.policy.mode.value,
            "gate": self.gate.to_dict(),
            "stages": [
                {"name": s.name, "success": s.success, "duration_ms": s.duration_ms}
                for s in self.stages
            ],
            "completed_at": self.completed_at,
        }


class MetaPipeline:
    """The full META pipeline — one task, one worker, full verification.

    Usage::

        pipeline = MetaPipeline(
            worker=lambda prompt: "def answer():\n    return 42\n",
            workdir=Path("/tmp/meta-work"),
        )
        result = pipeline.run(MetaTask(
            task_id="T-1",
            objective="Implement answer() that returns 42",
            metadata={"verification_commands": ["python -c 'import mod; assert mod.answer() == 42'"]},
        ))
    """

    def __init__(
        self,
        *,
        worker: ModelCall,
        workdir: Optional[Path] = None,
        policy_router: Optional[PolicyRouter] = None,
        task_translator: Optional[TaskTranslator] = None,
        verification_gate: Optional[VerificationGate] = None,
        worker_registry: Optional[WorkerRegistry] = None,
        import_graph: Optional[ImportGraph] = None,
        file_index: Optional[Dict[str, Any]] = None,
        outcome_ledger: Optional[OutcomeLedger] = None,
    ) -> None:
        self._worker = worker
        self._workdir = workdir or Path("/tmp/meta-pipeline")
        self._policy_router = policy_router or PolicyRouter()
        self._task_translator = task_translator or TaskTranslator(
            context_compiler=ContextCompiler(
                file_index=file_index or {},
                graph=import_graph,
            ),
        )
        self._gate = verification_gate or VerificationGate()
        self._workers = worker_registry
        self._ledger = outcome_ledger

    def run(
        self,
        task: MetaTask,
        *,
        max_attempts: int = 3,
        worker_id: str = "injected-worker",
    ) -> FinalResult:
        """Run the full pipeline on *task*.

        Pipeline stages:
            1. POLICY — select execution mode
            2. TRANSLATE — compile task for worker
            3. EXECUTE — worker produces artifact
            4. VERIFY — independent verification
            5. (optional) RETRY — on failure, correct and re-run
        """
        stages: List[PipelineStage] = []
        workdir = self._workdir / task.task_id
        workdir.mkdir(parents=True, exist_ok=True)

        # Stage 1: POLICY.
        stage = PipelineStage(name="policy")
        policy = self._policy_router.route(task)
        stage.completed_at = _now()
        stage.output_summary = f"mode={policy.mode.value}"
        stages.append(stage)

        # Stage 2: TRANSLATE.
        stage = PipelineStage(name="translate")
        # Map context strategy to token budget.
        context_budget_map = {
            "FULL": 32768,
            "COMPILED": 8192,
            "MINIMAL": 4096,
        }
        max_tokens = context_budget_map.get(policy.shape.context.value, 8192)
        worker_profile = WorkerProfile(
            worker_id=worker_id,
            max_context_tokens=max_tokens,
        )
        model_task = self._task_translator.translate(task, worker=worker_profile)
        stage.completed_at = _now()
        stage.output_summary = f"context_files={len(model_task.context_files)}"
        stages.append(stage)

        # Stage 3-5: EXECUTE + VERIFY loop.
        last_gate: Optional[GateResult] = None
        last_worker_result: Optional[WorkerResult] = None
        prompt = self._build_prompt(model_task)

        for attempt in range(1, max_attempts + 1):
            # Stage 3: EXECUTE.
            stage = PipelineStage(name=f"execute_a{attempt}")
            try:
                raw_output = self._worker(prompt)
                # Write artifact to workdir so verification commands can read it.
                artifact_file = workdir / "artifact.py"
                artifact_file.write_text(raw_output + "\n", encoding="utf-8")
                wr = WorkerResult(
                    task_id=task.task_id,
                    worker_id=worker_id,
                    status=WorkerStatus.PRODUCED,
                    artifact_ref=raw_output,
                    attempt=attempt,
                )
            except Exception as exc:  # noqa: BLE001
                wr = WorkerResult(
                    task_id=task.task_id,
                    worker_id=worker_id,
                    status=WorkerStatus.ERROR,
                    error_class=type(exc).__name__,
                    message=str(exc),
                    attempt=attempt,
                )
                stage.success = False
                stage.output_summary = f"error: {type(exc).__name__}"

            last_worker_result = wr
            stage.completed_at = _now()
            stages.append(stage)

            # Stage 4: VERIFY.
            stage = PipelineStage(name=f"verify_a{attempt}")
            gate = self._gate.verify(
                task=task,
                worker_result=wr,
                policy=policy,
                workdir=workdir,
            )
            last_gate = gate
            stage.completed_at = _now()
            stage.output_summary = f"verdict={gate.verdict.value}"
            stages.append(stage)

            if gate.passed:
                result = FinalResult(
                    task_id=task.task_id,
                    gate=gate,
                    policy=policy,
                    stages=stages,
                    worker_result=wr,
                    verification=gate.verification,
                    metadata={"attempts": attempt},
                )
                self._record_outcome(task, result, policy, wr, attempt)
                return result

            # Stage 5: RETRY (if allowed).
            if attempt < max_attempts and gate.should_retry:
                stage = PipelineStage(name=f"retry_a{attempt}")
                prompt = self._build_correction_prompt(model_task, gate)
                stage.completed_at = _now()
                stage.output_summary = "retry with correction"
                stages.append(stage)
            elif gate.should_escalate:
                break  # don't retry — escalate
            else:
                break  # don't retry — repair not suggested

        # All attempts exhausted.
        final_gate = last_gate or GateResult(
            task_id=task.task_id,
            worker_id=worker_id,
            verdict=Verdict.FAIL,
            verification=VerificationResult(task_id=task.task_id, verdict=Verdict.FAIL),
            reason="no attempts produced a result",
        )
        result = FinalResult(
            task_id=task.task_id,
            gate=final_gate,
            policy=policy,
            stages=stages,
            worker_result=last_worker_result,
            metadata={"attempts": max_attempts, "exhausted": True},
        )
        self._record_outcome(task, result, policy, last_worker_result, max_attempts)
        return result

    def _build_prompt(self, model_task: Any) -> str:
        """Build a prompt from the translated ModelTask."""
        parts = [f"objective: {model_task.objective}"]

        if model_task.context_files:
            parts.append(f"files: {', '.join(model_task.context_files)}")

        if model_task.tool_policy.allowed:
            parts.append(f"allowed tools: {', '.join(model_task.tool_policy.allowed)}")
        if model_task.tool_policy.forbidden:
            parts.append(f"forbidden tools: {', '.join(model_task.tool_policy.forbidden)}")

        if model_task.constraints:
            constraint_lines = [f"  {k}: {v}" for k, v in model_task.constraints.items()]
            parts.append("constraints:\n" + "\n".join(constraint_lines))

        if model_task.verification_commands:
            parts.append(f"must pass: {', '.join(model_task.verification_commands)}")

        parts.append("Output only the code, no prose, no markdown fences.")
        return "\n\n".join(parts)

    def _build_correction_prompt(self, model_task: Any, gate: GateResult) -> str:
        """Build a correction prompt from the failed verification."""
        base = self._build_prompt(model_task)
        failed_checks = [
            c for c in gate.verification.checks if not c.passed
        ]
        if failed_checks:
            correction_lines = [
                "Your previous attempt did not pass these checks. Fix the code so they pass.",
            ]
            for c in failed_checks:
                correction_lines.append(f"- {c.name} failed:\n{c.detail}")
            return base + "\n\n" + "\n".join(correction_lines)
        return base

    def _record_outcome(
        self,
        task: MetaTask,
        result: FinalResult,
        policy: ExecutionPolicy,
        worker_result: Optional[WorkerResult],
        attempts: int,
    ) -> None:
        """Feed the outcome to the ledger if one is configured."""
        if self._ledger is None:
            return

        # Extract timing from stages.
        total_ms = sum(s.duration_ms for s in result.stages)
        v_score = result.gate.confidence
        if result.gate.checks_run > 0:
            v_score = result.gate.checks_passed / result.gate.checks_run

        outcome = PipelineOutcome(
            task_id=task.task_id,
            task_objective=task.objective[:200],
            task_type=task.metadata.get("task_type", "unknown"),
            worker_id=worker_result.worker_id if worker_result else "unknown",
            execution_mode=policy.mode.value,
            verdict=result.verdict.value,
            verification_score=v_score,
            attempts=attempts,
            latency_ms=total_ms,
            failure_class=worker_result.error_class if worker_result else "",
            failure_message=worker_result.message if worker_result else "",
        )
        self._ledger.record(outcome)
