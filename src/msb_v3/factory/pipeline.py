"""Software Factory pipeline (spec §4.2.6, P3; §8-10, §31 items 25-30).

    issue → classify → plan (MoIE risks) → build (isolated worktree) →
    test (real command) → review (independent MoIE + code graph) →
    verify (acceptance vs evidence) → verdict

Every stage appends a sha256 of its evidence to the run's evidence_chain,
so a claim ("tests passed") is re-derivable from the chain. Verdicts:

    MERGED       build ok + tests passed + review not BLOCK + verification PASS
    NEEDS_WORK   build ok but tests failed / verification FAIL / review CONCERN
    BLOCKED      independent review BLOCK (anti-fabrication gate)
    FAILED       build failed or no builder produced a change

The original repo is never mutated: the builder works in a temp worktree
that the factory removes when done.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from typing import Any, Optional

from msb_v3.factory.builders import (
    Builder,
    CliAgentBuilder,
    compute_changes,
    create_worktree,
)
from msb_v3.factory.classifier import classify
from msb_v3.factory.models import FactoryRun, Issue
from msb_v3.factory.planner import plan as plan_issue
from msb_v3.factory.reviewer import areview as areview_change
from msb_v3.factory.test_runner import run_tests
from msb_v3.factory.verifier import verify as verify_change

logger = logging.getLogger(__name__)


def _stage_hash(stage: str, payload: Any) -> str:
    raw = json.dumps({"stage": stage, "payload": payload}, default=str, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class SoftwareFactory:
    """One issue → one FactoryRun, with injectable seams (builder, moie,
    codegraph, test command) so tests and deployments pin each stage."""

    def __init__(
        self,
        *,
        builder: Optional[Builder] = None,
        moie: Any = None,
        codegraph: Any = None,
        test_command: Optional[str] = None,
        keep_worktree: bool = False,
        reviewer_panel: Any = None,
    ) -> None:
        self._builder = builder
        self._moie = moie
        self._codegraph = codegraph
        self._test_command = test_command
        self._keep_worktree = keep_worktree
        self._reviewer_panel = reviewer_panel

    async def process_issue(
        self,
        issue: Issue,
        *,
        repo: str = "",
        timeout_s: float = 300.0,
    ) -> FactoryRun:
        run = FactoryRun(issue=issue)
        chain: list[str] = []

        # 1. classify
        try:
            classification = classify(issue, codegraph=self._codegraph, repo=repo or issue.repo)
            run.classification = classification
        except Exception as exc:  # noqa: BLE001
            run.error = f"classify failed: {type(exc).__name__}: {exc}"
            return run
        chain.append(_stage_hash("classify", classification.as_dict()))

        # 2. plan (with MoIE risks)
        try:
            planned = plan_issue(issue, classification, moie=self._moie)
        except Exception as exc:  # noqa: BLE001
            run.error = f"plan failed: {type(exc).__name__}: {exc}"
            return run
        run.plan = planned
        chain.append(_stage_hash("plan", planned.as_dict()))

        # 3. build in an isolated worktree (original repo untouched)
        builder = self._builder or CliAgentBuilder()
        # Reviewer-panel invariant, enforced at the point of use: the worker
        # that built the change may never also review it.
        if self._reviewer_panel is not None:
            builder_model = getattr(builder, "model", "") or builder.builder_id
            if builder_model in self._reviewer_panel.reviewer_models:
                run.verdict = "BLOCKED"
                run.error = (
                    f"builder model {builder_model!r} may not also be a reviewer "
                    f"(builder != reviewer invariant)"
                )
                return run
        worktree = ""
        try:
            worktree = create_worktree(repo or issue.repo)
            build = await builder.build(planned, worktree, repo_hint=repo or issue.repo)
            if build.ok:
                changed, diff = compute_changes(repo or issue.repo, worktree)
                build.changed_files = changed
                build.diff = diff
            run.build = build
            chain.append(_stage_hash("build", build.as_dict()))
        except Exception as exc:  # noqa: BLE001
            run.error = f"build failed: {type(exc).__name__}: {exc}"
            run.build = None
            run.evidence_chain = chain
            self._cleanup(worktree)
            return run

        # 4. test (real command, real evidence) — only when the build is ok
        if build.ok:
            try:
                test = await run_tests(worktree, command=self._test_command, timeout_s=timeout_s)
                run.test = test
                chain.append(_stage_hash("test", test.as_dict()))
            except Exception as exc:  # noqa: BLE001
                run.test.passed = False
                run.test.ran = True
                run.test.output_head = f"test stage failed: {type(exc).__name__}: {exc}"
                chain.append(_stage_hash("test", run.test.as_dict()))

        # 5. independent review (MoIE + code graph) — not the builder's claim
        try:
            moie = self._moie
            if moie is None and self._reviewer_panel is not None:
                moie = self._reviewer_panel.controller()
            reviewed = await areview_change(
                planned, build,
                moie=moie, codegraph=self._codegraph, repo=repo or issue.repo,
                high_impact=classification.severity in ("high", "critical"),
            )
            run.review = reviewed
            chain.append(_stage_hash("review", reviewed.as_dict()))
        except Exception as exc:  # noqa: BLE001
            reviewed = None
            run.error = run.error or f"review failed: {type(exc).__name__}: {exc}"

        # 6. verify acceptance criteria against observed evidence
        review_verdict = reviewed.verdict if reviewed else "BLOCK"
        try:
            run.verification = verify_change(planned, build, run.test, review_verdict=review_verdict)
            chain.append(_stage_hash("verify", run.verification.as_dict()))
        except Exception as exc:  # noqa: BLE001
            run.error = run.error or f"verify failed: {type(exc).__name__}: {exc}"

        # 7. verdict (fail-closed: a missing/blocked independent review blocks)
        if build.ok is False:
            run.verdict = "FAILED"
        elif reviewed is None or reviewed.verdict == "BLOCK":
            run.verdict = "BLOCKED"
        elif not run.test.ran or not run.test.passed:
            run.verdict = "NEEDS_WORK"
        elif run.verification.verdict == "FAIL":
            run.verdict = "NEEDS_WORK"
        elif run.verification.verdict == "UNVERIFIED":
            run.verdict = "NEEDS_WORK"
        elif reviewed is not None and reviewed.verdict == "CONCERN":
            run.verdict = "NEEDS_WORK"
        else:
            run.verdict = "MERGED"

        run.evidence_chain = chain
        self._cleanup(worktree)
        return run

    def _cleanup(self, worktree: str) -> None:
        if not worktree or self._keep_worktree:
            return
        try:
            shutil.rmtree(worktree, ignore_errors=True)
        except Exception:  # noqa: BLE001
            logger.debug("worktree cleanup failed for %s", worktree)
