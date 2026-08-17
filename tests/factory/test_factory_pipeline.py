"""Software Factory pipeline tests (spec §4.2.6, §8-10).

Real worktrees, real patch scripts, real pytest runs — the pipeline's
verdicts are decided by observed evidence, and these tests assert exactly
that (no mocks of the test runner or the worktree).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from msb_v3.factory import PatchBuilder, SoftwareFactory
from msb_v3.factory.classifier import classify
from msb_v3.factory.models import Issue
from msb_v3.factory.planner import plan


def _run(factory: SoftwareFactory, issue: Issue, repo: str):
    return asyncio.run(factory.process_issue(issue, repo=repo))


def test_compute_changes_emits_diff_for_new_file(tmp_path: Path) -> None:
    """The diff the reviewer reads must include NEW files. The live dogfood
    missed a seeded doc contradiction because a brand-new file produced an
    EMPTY diff (the old file was read and its missing-source OSError skipped
    the whole file) — the reviewer had no change to read. A new file must
    appear as a full +-diff."""
    from msb_v3.factory.builders import compute_changes, create_worktree

    src = tmp_path / "repo"
    src.mkdir()

    wt = create_worktree(str(src))
    (Path(wt) / "new.md").write_text("# New\ncontent\n")

    changed, diff = compute_changes(str(src), wt)
    assert "new.md" in changed
    assert "# New" in diff, "a NEW file must appear in the diff (was empty)"
    assert "content" in diff


# --- classify ---------------------------------------------------------------


def test_classify_bug():
    c = classify(Issue(title="Fix the crash when parsing empty input"))
    assert c.issue_type == "bug"
    assert c.severity == "medium"


def test_classify_security_wins():
    c = classify(Issue(title="Add a login page but fix the sql injection too", body="an injection bug"))
    assert c.issue_type == "security"


def test_classify_feature_and_labels():
    c = classify(Issue(title="Support exporting reports to CSV", labels=["P1", "feature"]))
    assert c.issue_type == "feature"
    assert c.severity == "high"


# --- plan -------------------------------------------------------------------


def test_plan_has_steps_and_moie_risks():
    p = plan(Issue(title="Deploy the migration with no rollback plan"), classify(Issue(title="x")))
    assert len(p.steps) >= 3
    assert any(s.step_id == "final" for s in p.steps)
    assert p.risks or p.assumptions  # MoIE contributed independent risk signal


def test_plan_bug_vs_feature_shapes():
    bug = plan(Issue(title="Fix the crash"), classify(Issue(title="Fix the crash")))
    feat = plan(Issue(title="Add a new endpoint"), classify(Issue(title="Add a new endpoint")))
    assert any("Reproduce" in s.title for s in bug.steps)
    assert not any("Reproduce" in s.title for s in feat.steps)


# --- full pipeline ----------------------------------------------------------


def test_factory_merges_good_change(repo, good_patch):
    issue = Issue(title="Add a multiply function", body="app needs mul(a, b)")
    run = _run(SoftwareFactory(builder=PatchBuilder(good_patch)), issue, str(repo))
    assert run.verdict == "MERGED", run.error
    assert run.build is not None and run.build.ok
    assert "app.py" in run.build.changed_files
    assert "test" in " ".join(run.build.changed_files).lower()
    assert run.test.ran and run.test.passed
    assert run.review is not None and run.review.verdict == "APPROVE"
    assert run.verification.verdict == "PASS"
    assert len(run.evidence_chain) >= 6  # classify, plan, build, test, review, verify
    # The original repo was never touched.
    assert "def mul" not in (repo / "app.py").read_text()
    assert "test_mul" not in (repo / "tests" / "test_app.py").read_text()


def test_factory_fails_on_broken_tests(repo, breaking_patch):
    run = _run(SoftwareFactory(builder=PatchBuilder(breaking_patch)), Issue(title="Fix add"), str(repo))
    assert run.verdict == "NEEDS_WORK"
    assert run.test.ran and run.test.passed is False
    assert run.verification.verdict == "FAIL"


def test_factory_blocks_noop_change(repo, noop_patch):
    run = _run(SoftwareFactory(builder=PatchBuilder(noop_patch)), Issue(title="Add a feature"), str(repo))
    assert run.verdict == "BLOCKED"
    assert run.review is not None and run.review.verdict == "BLOCK"
    assert any("no files changed" in f.message for f in run.review.findings)


def test_factory_failed_on_build_error(repo, failing_patch):
    run = _run(SoftwareFactory(builder=PatchBuilder(failing_patch)), Issue(title="Add a feature"), str(repo))
    assert run.verdict == "FAILED"
    assert run.build is not None and run.build.ok is False


def test_factory_blocks_dangerous_issue_via_moie(repo, good_patch):
    # Benign change, dangerous issue: the independent MoIE review must BLOCK.
    run = _run(
        SoftwareFactory(builder=PatchBuilder(good_patch)),
        Issue(title="Disable auth and bind 0.0.0.0 so the service is unauthenticated"),
        str(repo),
    )
    assert run.verdict == "BLOCKED"
    assert run.review is not None and run.review.verdict == "BLOCK"
    assert run.review.moie_verdict == "BLOCK"


def test_factory_no_test_command_is_unverified(repo, good_patch, monkeypatch, tmp_path):
    # A repo with no detectable test tooling must be UNVERIFIED, not a pass.
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "app.py").write_text("x = 1\n")

    async def noop_build(plan, worktree, *, repo_hint=""):
        from msb_v3.factory.models import BuildResult

        (Path(worktree) / "app.py").write_text("x = 2\n")
        return BuildResult(ok=True, worktree=worktree)

    class _NoopBuilder:
        builder_id = "noop"

        async def build(self, plan, worktree, *, repo_hint=""):
            return await noop_build(plan, worktree, repo_hint=repo_hint)

    run = _run(SoftwareFactory(builder=_NoopBuilder()), Issue(title="Tweak a value"), str(plain))
    assert run.test.ran is False
    assert run.verification.verdict == "UNVERIFIED"
    assert run.verdict == "NEEDS_WORK"


def test_factory_unavailable_builder_fails_honestly(repo):
    from msb_v3.agent.providers import CliAgentProvider
    from msb_v3.factory import CliAgentBuilder

    provider = CliAgentProvider(("/no/such/binary",), provider_id="cli.missing")
    run = _run(SoftwareFactory(builder=CliAgentBuilder(provider=provider)), Issue(title="Add a feature"), str(repo))
    assert run.verdict == "FAILED"
    assert "unavailable" in (run.build.error or "")


def test_factory_evidence_chain_references_stages(repo, good_patch):
    run = _run(SoftwareFactory(builder=PatchBuilder(good_patch)), Issue(title="Add a multiply function"), str(repo))
    assert len(run.evidence_chain) == 6
    assert all(len(h) == 16 for h in run.evidence_chain)


# --- reviewer (independent review, spec §4.2.6 / §9) ------------------------


def test_review_benign_severity_does_not_escalate():
    # high_impact is derived from the issue severity: a benign change with a
    # benign issue must not be escalated into a fabricated CONCERN/BLOCK.
    from msb_v3.factory.models import BuildResult
    from msb_v3.factory.reviewer import review as review_change

    p = plan(Issue(title="Add a multiply function"), classify(Issue(title="Add a multiply function")))
    build = BuildResult(ok=True, changed_files=["app.py"], diff="+def mul(a, b): return a * b\n")

    class _BenignMoIE:
        def analyze(self, claim, *, context=None):
            class _D:
                verdict = "APPROVE"
                blocked = False
                meta_critique = ""
                recommended_actions = []
                ids = type("I", (), {"depth_score": 0.5})()

            return _D()

    r = review_change(p, build, moie=_BenignMoIE(), high_impact=False)
    assert r.verdict == "APPROVE"
    assert r.moie_verdict == "APPROVE"
    assert not any(f.severity == "concern" for f in r.findings)


def test_review_conditional_always_surfaces_concern():
    # When the independent MoIE inversion is CONDITIONAL, the review must
    # surface a concern even if no recommended action text is available —
    # never silently approve under a conditional verdict.
    from msb_v3.factory.models import BuildResult
    from msb_v3.factory.reviewer import review as review_change

    p = plan(Issue(title="Add a multiply function"), classify(Issue(title="Add a multiply function")))
    build = BuildResult(ok=True, changed_files=["app.py"], diff="+def mul(a, b): return a * b\n")

    class _ConditionalMoIE:
        def analyze(self, claim, *, context=None):
            class _D:
                verdict = "CONDITIONAL"
                blocked = False
                meta_critique = "no rollback plan for the change"
                recommended_actions = []
                ids = type("I", (), {"depth_score": 0.8})()

            return _D()

    r = review_change(p, build, moie=_ConditionalMoIE(), high_impact=True)
    assert r.verdict == "CONCERN"
    assert r.moie_verdict == "CONDITIONAL"
    assert any(f.severity == "concern" for f in r.findings)


# --- diverse LLM reviewer panel (builder != reviewer) -----------------------


def _safe_client_factory(model):
    from types import SimpleNamespace

    class _C:
        def __init__(self, model):
            self.model = model

        def generate(self, prompt, *, system=None, **kw):
            return SimpleNamespace(text="VERDICT: SAFE", model=self.model)

    return _C(model)


def test_factory_review_panel_llm_reviewers_flow(repo, good_patch):
    from msb_v3.moie import build_diverse_reviewer_panel

    panel = build_diverse_reviewer_panel(
        builder_model="patch",
        models=["qwen3:8b", "deepseek-r1"],
        client_factory=_safe_client_factory,
    )
    run = _run(
        SoftwareFactory(builder=PatchBuilder(good_patch), reviewer_panel=panel),
        Issue(title="Add a multiply function"),
        str(repo),
    )
    assert run.verdict == "MERGED", run.error
    assert run.review is not None
    assert run.review.reviewer_models == ["qwen3:8b", "deepseek-r1"]
    assert run.review.independent is True


def test_factory_rejects_builder_as_reviewer(repo, good_patch):
    # The panel was built for a different builder ("patch"), so its reviewers
    # legitimately include "claude" — but the factory runs a Claude CLI
    # builder. The factory boundary must fail closed (BLOCKED) rather than
    # let the builder's own model review it.
    from msb_v3.factory import CliAgentBuilder
    from msb_v3.moie import build_diverse_reviewer_panel

    panel = build_diverse_reviewer_panel(
        builder_model="patch",
        models=["claude", "qwen3:8b"],
        client_factory=_safe_client_factory,
    )
    run = _run(
        SoftwareFactory(builder=CliAgentBuilder(), reviewer_panel=panel),
        Issue(title="Add a multiply function"),
        str(repo),
    )
    assert run.verdict == "BLOCKED"
    assert "may not also be a reviewer" in (run.error or "")
