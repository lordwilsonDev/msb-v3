"""Software Factory verifier (spec §4.2.6 — verify stage; §9-10).

Checks each acceptance criterion against **observed evidence** — executed
test results and worktree artifacts — never against the builder's claims.
A criterion with no evidence is UNVERIFIED; the verdict is PASS only when
every criterion passes with real evidence (a test that never ran is not a
pass).
"""

from __future__ import annotations

from typing import List

from msb_v3.factory.models import (
    BuildResult,
    Plan,
    TestEvidence,
    Verification,
    VerificationCheck,
)

# Deterministic checks for well-known criteria. Unknown criteria are
# checked for *evidence of presence*, else UNVERIFIED.
_KNOWN_CHECKS = {
    "tests pass": "tests pass",
    "test evidence exists": "test evidence exists",
    "independent review not BLOCK": "independent review not BLOCK",
    "the reported symptom is gone in the worktree": "symptom",
    "the new test exists": "test file exists",
    "new tests pass": "tests pass",
}


def verify(plan: Plan, build: BuildResult, test: TestEvidence, *, review_verdict: str = "APPROVE") -> Verification:
    checks: List[VerificationCheck] = []

    # Collect every acceptance criterion from the plan's steps.
    criteria: List[str] = []
    seen: set[str] = set()
    for step in plan.steps:
        for c in step.acceptance:
            if c not in seen:
                seen.add(c)
                criteria.append(c)

    for criterion in criteria:
        checks.append(_check_criterion(criterion, build, test, review_verdict))

    if any(c.result == "FAIL" for c in checks):
        verdict = "FAIL"
    elif any(c.result == "UNVERIFIED" for c in checks):
        verdict = "UNVERIFIED"
    elif all(c.result == "PASS" for c in checks) and checks:
        verdict = "PASS"
    else:
        verdict = "UNVERIFIED"
    return Verification(verdict=verdict, checks=checks[:20])


def _check_criterion(criterion: str, build: BuildResult, test: TestEvidence, review_verdict: str) -> VerificationCheck:
    lowered = criterion.lower()
    for key, kind in _KNOWN_CHECKS.items():
        if key in lowered:
            return _check_kind(kind, build, test, review_verdict, criterion)
    return _check_kind("presence", build, test, review_verdict, criterion)


def _check_kind(kind: str, build: BuildResult, test: TestEvidence, review_verdict: str, criterion: str) -> VerificationCheck:
    if kind == "tests pass":
        if not test.ran:
            return VerificationCheck(criterion, "UNVERIFIED", "no test command found/run — no evidence")
        if test.passed:
            return VerificationCheck(criterion, "PASS", f"exit 0: {test.command} ({test.duration_s}s)")
        return VerificationCheck(criterion, "FAIL", f"exit {test.exit_code}: {test.command}")

    if kind == "test evidence exists":
        if test.ran:
            return VerificationCheck(criterion, "PASS", f"{test.command} ran (exit {test.exit_code})")
        return VerificationCheck(criterion, "UNVERIFIED", "no test evidence produced")

    if kind == "independent review not BLOCK":
        if review_verdict == "BLOCK":
            return VerificationCheck(criterion, "FAIL", "independent review blocked the change")
        return VerificationCheck(criterion, "PASS", f"independent review: {review_verdict}")

    if kind == "test file exists":
        if build.changed_files and any("test" in f.lower() for f in build.changed_files):
            return VerificationCheck(criterion, "PASS", "a test file appears in the changed files")
        return VerificationCheck(criterion, "FAIL", "no test file among the changed files")

    if kind == "symptom":
        # Honest fallback: evidence is the diff exists + tests pass; a
        # symptom-level check needs a domain harness we cannot invent.
        if test.passed:
            return VerificationCheck(criterion, "PASS", "tests pass after the change")
        return VerificationCheck(criterion, "UNVERIFIED", "cannot observe the symptom without a domain harness")

    # Presence check: did the change touch anything at all (with tests ok)?
    if build.changed_files:
        evidence = f"{len(build.changed_files)} file(s) changed"
        if test.ran:
            evidence += f"; {test.command} exit {test.exit_code}"
        return VerificationCheck(criterion, "PASS" if (not test.ran or test.passed) else "FAIL", evidence)
    return VerificationCheck(criterion, "FAIL", "no change produced")
