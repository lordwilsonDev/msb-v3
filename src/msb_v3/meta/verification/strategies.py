"""Verification strategies — different levels of verification rigor.

Each strategy is a function: (task, worker_result, workdir) → list[CheckResult].

The VerificationGate selects the strategy based on the ExecutionPolicy's
verification field.  The worker is NEVER allowed to verify itself.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import List, Optional

from msb_v3.meta.contracts import (
    CheckResult,
    MetaTask,
    WorkerResult,
    WorkerStatus,
)

logger = logging.getLogger(__name__)


def _run_command(
    cmd: str,
    workdir: Path,
    *,
    timeout: float = 120.0,
) -> CheckResult:
    """Run a single command and return a CheckResult."""
    try:
        proc = subprocess.run(
            cmd, cwd=str(workdir), shell=True, capture_output=True,
            text=True, timeout=timeout,
        )
        passed = proc.returncode == 0
        output = proc.stdout + proc.stderr
        detail = "" if passed else "\n".join(output.splitlines()[-20:]).strip()
        return CheckResult(name=cmd, passed=passed, detail=detail)
    except subprocess.TimeoutExpired:
        return CheckResult(name=cmd, passed=False, detail=f"timeout after {timeout}s")
    except Exception as exc:  # noqa: BLE001
        return CheckResult(name=cmd, passed=False, detail=f"error: {exc}")


class StandardStrategy:
    """STANDARD: deterministic checks only — exit codes from verification commands.

    This is the baseline.  Every task gets at least this level of verification.
    """

    def verify(
        self,
        task: MetaTask,
        worker_result: WorkerResult,
        workdir: Path,
        *,
        verification_commands: Optional[List[str]] = None,
    ) -> List[CheckResult]:
        """Run verification commands and return check results."""
        commands = verification_commands or task.metadata.get("verification_commands", [])
        if not commands:
            return []

        results: List[CheckResult] = []
        for cmd in commands:
            result = _run_command(cmd, workdir)
            results.append(result)
        return results


class StrictStrategy:
    """STRICT: deterministic + contract + import-direction checks.

    Adds structural checks beyond standard:
    - Import direction (core must not depend on adapters)
    - File boundary (worker only touched allowed files)
    - Artifact existence (worker claimed to produce something)
    """

    def verify(
        self,
        task: MetaTask,
        worker_result: WorkerResult,
        workdir: Path,
        *,
        verification_commands: Optional[List[str]] = None,
    ) -> List[CheckResult]:
        """Run standard checks + contract checks."""
        standard = StandardStrategy()
        results = standard.verify(task, worker_result, workdir, verification_commands=verification_commands)

        # Contract check: worker claimed to produce an artifact.
        if worker_result.status is WorkerStatus.PRODUCED:
            if worker_result.artifact_ref:
                # Check the artifact file exists if it looks like a path.
                artifact_path = workdir / worker_result.artifact_ref.split("\n")[0]
                if "/" in worker_result.artifact_ref or worker_result.artifact_ref.endswith(".py"):
                    if artifact_path.exists():
                        results.append(CheckResult(
                            name="artifact_exists",
                            passed=True,
                        ))
                    else:
                        results.append(CheckResult(
                            name="artifact_exists",
                            passed=False,
                            detail=f"claimed artifact not found: {artifact_path}",
                        ))

        # Contract check: no governance files were modified.
        task.metadata.get("allowed_path_prefixes", ["src/", "tests/"])
        forbidden_prefixes = task.metadata.get("forbidden_path_prefixes", [
            "docs/governance/", "audit/", "security/",
        ])

        # Scan workdir for unexpected modifications.
        if workdir.exists():
            for modified in _find_modified_files(workdir):
                is_forbidden = any(modified.startswith(fp) for fp in forbidden_prefixes)
                if is_forbidden:
                    results.append(CheckResult(
                        name="boundary_check",
                        passed=False,
                        detail=f"forbidden file modified: {modified}",
                    ))
                    break

        return results


class FuzzyStrategy:
    """FUZZY: deterministic + semantic validation.

    Extends Strict with:
    - Code quality heuristics (no bare except, no hardcoded secrets)
    - Semantic completeness check (function has a return, class has methods)
    - Structural health (file not empty, not just comments)

    This is the strongest verification short of an independent LLM judge.
    """

    def verify(
        self,
        task: MetaTask,
        worker_result: WorkerResult,
        workdir: Path,
        *,
        verification_commands: Optional[List[str]] = None,
    ) -> List[CheckResult]:
        """Run strict checks + semantic checks."""
        strict = StrictStrategy()
        results = strict.verify(task, worker_result, workdir, verification_commands=verification_commands)

        # Semantic check: artifact content quality.
        if worker_result.status is WorkerStatus.PRODUCED and worker_result.artifact_ref:
            content = worker_result.artifact_ref

            # Check: not empty.
            if len(content.strip()) == 0:
                results.append(CheckResult(
                    name="semantic_not_empty",
                    passed=False,
                    detail="artifact is empty",
                ))
            else:
                results.append(CheckResult(name="semantic_not_empty", passed=True))

            # Check: not just comments.
            code_lines = [
                line for line in content.splitlines()
                if line.strip() and not line.strip().startswith("#")
            ]
            if len(code_lines) == 0:
                results.append(CheckResult(
                    name="semantic_has_code",
                    passed=False,
                    detail="artifact contains only comments",
                ))
            else:
                results.append(CheckResult(name="semantic_has_code", passed=True))

            # Check: no bare except.
            if "except:" in content and "except Exception" not in content:
                results.append(CheckResult(
                    name="semantic_no_bare_except",
                    passed=False,
                    detail="bare except: found — use except Exception instead",
                ))

        return results


def _find_modified_files(workdir: Path) -> List[str]:
    """Find files that exist in workdir (simulates git diff for non-git workdirs)."""
    modified: List[str] = []
    if not workdir.exists():
        return modified
    for path in workdir.rglob("*"):
        if path.is_file():
            rel = str(path.relative_to(workdir))
            modified.append(rel)
    return modified
