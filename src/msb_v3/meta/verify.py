"""Meta-System verification seam: run checks -> VerificationResult.

``classify_check`` and ``verdict_from_checks`` are built by qwen3:8b;
``run_checks`` (subprocess I/O, fail-closed) is the checker's. Completion is
decided here, never by the worker (blueprint M5/M6).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from msb_v3.meta.contracts import CheckResult, Verdict, VerificationResult


def classify_check(name: str, returncode: int, output: str) -> CheckResult:  # qwen3:8b (V1), verbatim
    passed = returncode == 0
    if passed:
        detail = ""
    else:
        lines = output.split("\n")
        last_lines = lines[-20:]
        detail = "\n".join(last_lines).strip()
    return CheckResult(name=name, passed=passed, detail=detail)


def verdict_from_checks(checks: list[CheckResult]) -> Verdict:  # qwen3:8b (V2), verbatim
    if not checks:
        return Verdict.EXPECTED_SKIP
    if all(check.passed for check in checks):
        return Verdict.PASS
    else:
        return Verdict.FAIL


def run_checks(
    task_id: str,
    workdir: Path,
    commands: list[str],
    *,
    timeout: float = 300.0,
) -> VerificationResult:
    """Run each shell command in ``workdir``; a non-zero exit (or a timeout)
    is a failed check. Structural only — no model in the loop."""
    checks: list[CheckResult] = []
    for cmd in commands:
        try:
            proc = subprocess.run(
                cmd, cwd=str(workdir), shell=True, capture_output=True,
                text=True, timeout=timeout,
            )
            rc, out = proc.returncode, (proc.stdout + proc.stderr)
        except subprocess.TimeoutExpired:
            rc, out = 124, f"timed out after {timeout}s"
        checks.append(classify_check(cmd, rc, out))
    return VerificationResult(
        task_id=task_id,
        verdict=verdict_from_checks(checks),
        checks=checks,
    )
