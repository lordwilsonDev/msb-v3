"""Meta-System build loop (driver): render -> execute -> write -> verify ->
(correct -> retry)*.

The smallest thing that turns the hand-run loop into code. No auto-translation
(the strong model / human writes the MSL), no scheduler wiring yet — one MSL,
one target file, N attempts. Every attempt yields the same three record types
the eval layer will consume: WorkerResult, VerificationResult, FailureRecord.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List

from msb_v3.meta.contracts import (
    MSL,
    FailureRecord,
    Verdict,
    VerificationResult,
    WorkerResult,
    WorkerStatus,
)
from msb_v3.meta.verify import run_checks
from msb_v3.meta.worker import parse_worker_response, render_prompt

ModelCall = Callable[[str], str]


@dataclass
class BuildOutcome:
    msl_id: str
    target: str
    verdict: Verdict
    attempts: int
    worker_results: List[WorkerResult] = field(default_factory=list)
    verifications: List[VerificationResult] = field(default_factory=list)
    failures: List[FailureRecord] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.verdict is Verdict.PASS


def _correction_suffix(vr: VerificationResult) -> str:
    failed = [c for c in vr.checks if not c.passed]
    lines = [f"- {c.name} failed:\n{c.detail}" for c in failed]
    return (
        "\n\nYour previous attempt did not pass these checks. Fix the code so "
        "they pass. Do not change anything else.\n" + "\n".join(lines)
    )


def build_module(
    msl: MSL,
    workdir: Path,
    target: str,
    *,
    model_call: ModelCall,
    verify_commands: List[str],
    worker_id: str = "qwen3:8b",
    max_attempts: int = 3,
) -> BuildOutcome:
    """Drive one MSL to a verdict. ``model_call`` is any ``str -> str``
    (``worker.call_ollama`` is the local default); ``target`` is the file path
    under ``workdir`` the artifact is written to."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    out = BuildOutcome(msl_id=msl.msl_id, target=target, verdict=Verdict.FAIL, attempts=0)
    prompt = render_prompt(msl)

    for attempt in range(1, max_attempts + 1):
        out.attempts = attempt
        try:
            raw = model_call(prompt)
        except Exception as exc:  # noqa: BLE001 — fail-closed, recorded not raised
            wr = WorkerResult(
                task_id=msl.source_task_id, worker_id=worker_id,
                status=WorkerStatus.ERROR, attempt=attempt,
                error_class=type(exc).__name__, message=str(exc),
            )
            out.worker_results.append(wr)
            break

        wr = parse_worker_response(raw, msl.source_task_id, worker_id)
        wr.attempt = attempt
        out.worker_results.append(wr)
        if wr.status is not WorkerStatus.PRODUCED:
            break

        (workdir / target).write_text(wr.artifact_ref + "\n", encoding="utf-8")
        vr = run_checks(msl.source_task_id, workdir, verify_commands)
        vr.worker_id = worker_id
        vr.attempt = attempt
        out.verifications.append(vr)

        if vr.verdict is Verdict.PASS:
            out.verdict = Verdict.PASS
            return out

        out.failures.append(FailureRecord(
            failure_id=f"{msl.msl_id}-a{attempt}",
            task_id=msl.source_task_id,
            symptom="; ".join(c.name for c in vr.checks if not c.passed),
            evidence=[c.detail for c in vr.checks if not c.passed],
            repair_scope=[target],
            source_verification_task_id=msl.source_task_id,
        ))
        if attempt < max_attempts:
            prompt = render_prompt(msl) + _correction_suffix(vr)

    out.verdict = out.verifications[-1].verdict if out.verifications else Verdict.FAIL
    return out
