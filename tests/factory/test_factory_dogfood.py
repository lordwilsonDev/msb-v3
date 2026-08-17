"""M4 — factory dogfood (the Convergence blueprint's self-building proof).

MSB's software factory should be able to build, review (with a genuinely
diverse reviewer), verify, and merge a real change — and a seeded defect
must be caught by the reviewer, not passed.

This suite reuses the real pipeline (PatchBuilder + real worktree +
real pytest) with the diverse-LLM reviewer panel. The client factory is
deterministic (no live model), but the *diversity invariant* is real and
enforced: builder ∉ reviewers and reviewers pairwise distinct. Models are
recorded on the Review so the evidence chain carries who reviewed with
which model — the M4 "diverse review is real" requirement.

Exit evidence (M4): a full factory run artifact (request → generated
change → review → verification → merge decision), a reviewer that catches
a seeded defect, and no abandoned worktrees.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from msb_v3.factory import PatchBuilder, SoftwareFactory
from msb_v3.factory.models import Issue


def _run(factory: SoftwareFactory, issue: Issue, repo: str):
    return asyncio.run(factory.process_issue(issue, repo=repo))


def _client_factory(verdict: str = "SAFE", model_label: str = ""):
    """A deterministic model-backed client returning a fixed verdict.

    ``model_label`` lets a test vary the model per reviewer — the point of
    the diversity record. The verdict text is the *model's* answer; the
    panel parses it exactly like a real model's output."""

    def _make(model: str) -> Any:
        class _C:
            def __init__(self, model: str) -> None:
                self.model = model

            def generate(self, prompt: str, *, system: str | None = None, **kw: Any) -> Any:
                text = f"VERDICT: {verdict}\nRISK: {verdict.lower()} check\n"
                return SimpleNamespace(text=text, model=self.model)

        return _C(f"{model_label}{model}")

    return _make


def _diverse_panel(builder_model: str, models: list[str], verdict: str = "SAFE"):
    from msb_v3.moie import build_diverse_reviewer_panel

    return build_diverse_reviewer_panel(
        builder_model=builder_model,
        models=models,
        client_factory=_client_factory(verdict=verdict),
    )


# ---------------------------------------------------------------------------
# C1 — one real change through the factory, diverse review, merge decision
# ---------------------------------------------------------------------------

def test_factory_dogfood_full_path_merges_with_diverse_review(repo, good_patch) -> None:
    """A real change (patch applied to a real worktree, real pytest run)
    through the factory with a diverse reviewer panel lands as MERGED, and
    the review records the distinct reviewer models (evidence chain carries
    who reviewed with which model)."""
    panel = _diverse_panel(builder_model="patch", models=["qwen3:8b", "deepseek-r1:8b"])
    run = _run(
        SoftwareFactory(builder=PatchBuilder(good_patch), reviewer_panel=panel),
        Issue(title="Add a multiply function"),
        str(repo),
    )
    assert run.verdict == "MERGED", run.error
    assert run.review is not None
    # C2 — diverse review is real: the panel ran distinct reviewer models
    # and the builder is not among them.
    assert run.review.reviewer_models == ["qwen3:8b", "deepseek-r1:8b"]
    assert run.review.independent is True
    # The evidence chain covers the stages (classify -> plan -> review ->
    # verify -> merge).
    assert run.classification is not None
    assert run.plan is not None
    assert run.verification is not None


# ---------------------------------------------------------------------------
# C2 — builder must never review itself (diversity invariant, fail-closed)
# ---------------------------------------------------------------------------

def test_factory_dogfood_rejects_builder_as_reviewer(repo, good_patch) -> None:
    """If a builder's own model would review its change, the factory BLOCKS
    rather than letting the builder rubber-stamp itself (the one invariant
    with real teeth)."""
    from msb_v3.factory import CliAgentBuilder

    panel = _diverse_panel(builder_model="patch", models=["claude", "qwen3:8b"])
    run = _run(
        SoftwareFactory(builder=CliAgentBuilder(), reviewer_panel=panel),
        Issue(title="Add a multiply function"),
        str(repo),
    )
    assert run.verdict == "BLOCKED"
    assert "may not also be a reviewer" in (run.error or "")


# ---------------------------------------------------------------------------
# C3 — seeded defect caught by the reviewer
# ---------------------------------------------------------------------------

def test_factory_dogfood_reviewer_catches_seeded_defect(repo, good_patch) -> None:
    """The reviewer must catch a nontrivial defect. Here the model flags the
    change with a hard BLOCK (a security concern) — the factory must NOT
    merge it, and the review must carry the concern into the evidence."""
    panel = _diverse_panel(
        builder_model="patch",
        models=["qwen3:8b", "deepseek-r1:8b"],
        verdict="BLOCK",  # the seeded "reviewer spotted a problem"
    )
    run = _run(
        SoftwareFactory(builder=PatchBuilder(good_patch), reviewer_panel=panel),
        Issue(title="Add a multiply function"),
        str(repo),
    )
    assert run.verdict == "BLOCKED", run.error
    assert run.review is not None
    assert run.review.findings, "a BLOCK must carry a finding"
    # The concern is recorded in the evidence chain, not lost.
    assert any("block" in f.message.lower() for f in run.review.findings)


def test_factory_dogfood_reviewer_concern_surfaces_not_silent(repo, good_patch) -> None:
    """A CONCERN (conditional approval) must surface in the verdict — the
    factory may not silently treat a flagged change as clean."""
    panel = _diverse_panel(
        builder_model="patch",
        models=["qwen3:8b", "deepseek-r1:8b"],
        verdict="CONCERN",
    )
    run = _run(
        SoftwareFactory(builder=PatchBuilder(good_patch), reviewer_panel=panel),
        Issue(title="Add a multiply function"),
        str(repo),
    )
    # A concern is not a silent pass: either it blocked the merge or the
    # factory surfaced it in the review/verdict. The pipeline maps CONCERN
    # to a review finding that must be visible.
    assert run.review is not None
    if run.verdict != "MERGED":
        assert run.review.findings, "a non-merge must carry findings"
    else:
        # If it merged, the concern must at least be recorded on the review.
        assert any("concern" in f.message.lower() for f in run.review.findings)


# ---------------------------------------------------------------------------
# C3b — doc-level contradictions reach the reviewer (coherence lens, 2026-08-17)
# ---------------------------------------------------------------------------
# The live dogfood exposed a real gap: a seeded self-contradiction in the
# FINAL section of a doc was approved by both live LLM reviewers. Root cause:
# _build_prompt truncated the diff to 2000 chars, so the reviewer never saw
# the tail of the change. These tests pin the fix — the full bounded diff
# reaches the reviewer, and a coherence-reading reviewer catches the
# contradiction through the real pipeline.

def test_reviewer_prompt_carries_full_diff_tail() -> None:
    """The reviewer prompt must include the whole change, not a truncated
    head. A contradiction in the final section of a doc is the exact case
    the live dogfood missed — it must be visible to the reviewer."""
    from msb_v3.moie import LLMExpert

    expert = LLMExpert(
        expert_id="llm-coherence",
        name="Coherence Reviewer",
        description="coherence",
        lens="coherence",
        model="reviewer-model",
        # _build_prompt never calls the client, so a bare object suffices.
        client_factory=lambda model: SimpleNamespace(generate=lambda *a, **k: None, model=model),
    )
    # A diff longer than the old 2000-char cap, with the contradiction at
    # the very end (exactly how the live miss happened).
    tail = "case-safe/note.md — vault note written by the SAFE read-only case"
    body = ("# H\n\n" + ("x\n" * 1100))  # > 2000 chars before the tail
    diff = "diff --git a/doc.md b/doc.md\n" + body + "\n+ " + tail + "\n"
    assert len(diff) > 2000
    prompt = expert._build_prompt("goal: document the run", {"diff": diff, "changed_files": ["doc.md"]})
    assert tail in prompt, "the tail of the diff must reach the reviewer (was truncated at 2000 chars)"


def _contradiction_client_factory(verdict: str = "BLOCK"):
    """A reviewer that actually reads the diff and flags a seeded
    self-contradiction — the fake stands in for a coherence-reading model.
    It returns BLOCK only when BOTH halves of the contradiction are present
    in the prompt (i.e. the tail was not truncated); otherwise CONCERN."""

    def _make(model: str) -> Any:
        class _C:
            def __init__(self, model: str) -> None:
                self.model = model

            def generate(self, prompt: str, *, system: str | None = None, **kw: Any) -> Any:
                no_write = "no file written" in prompt
                wrote = "vault note written by the SAFE read-only case" in prompt
                if no_write and wrote:
                    return SimpleNamespace(
                        text=f"VERDICT: {verdict}\nRISK: internal contradiction — case 1 claims no write, artifacts section claims a write\n",
                        model=self.model,
                    )
                return SimpleNamespace(text="VERDICT: CONCERN\nRISK: could not see the full change\n", model=self.model)

        return _C(model)

    return _make


def test_deterministic_scan_catches_contradiction_even_when_llm_approves(repo, tmp_path) -> None:
    """The decisive live-dogfood regression: a SAFE (approving) reviewer
    must NOT get the contradiction through. The deterministic coherence scan
    runs on every review, so a weak model's approval cannot be the only
    guard — the change is held for review regardless."""
    script = tmp_path / "doc_patch.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'cd "$MSB_WORKTREE"\n'
        "mkdir -p docs\n"
        "cat > docs/run.md << 'EOF'\n"
        "# Core loop\n\n"
        "## Verdict cases\n\n"
        "1. SAFE read-only — PASS, 5 semantic hits, no file written.\n\n"
        "## Artifacts\n\n"
        "- `case-safe/note.md` — vault note written by the SAFE read-only case\n"
        "EOF\n"
    )
    script.chmod(0o755)

    # SAFE verdict: the LLM panel sees nothing wrong (the live 8B failure).
    panel = _diverse_panel(builder_model="patch", models=["qwen3:8b"], verdict="SAFE")
    run = _run(
        SoftwareFactory(builder=PatchBuilder(str(script)), reviewer_panel=panel),
        Issue(title="Document the core-loop run"),
        str(repo),
    )
    assert run.review is not None
    assert any("contradiction" in f.message.lower() for f in run.review.findings), (
        "the deterministic scan must flag the contradiction even though the LLM approved"
    )
    assert run.verdict != "MERGED", "a self-contradictory change must not merge"


def test_scan_doc_contradictions_flags_assert_and_negate() -> None:
    """The deterministic coherence scan flags a verb that appears both
    asserted and negated in the change — the exact seeded-defect shape from
    the live dogfood ("no file written" vs "vault note written")."""
    from msb_v3.factory.reviewer import scan_doc_contradictions

    contradictory = (
        "+1. SAFE read-only — PASS, 5 semantic hits, no file written.\n"
        "+- `case-safe/note.md` — vault note written by the SAFE read-only case\n"
    )
    findings = scan_doc_contradictions(contradictory)
    assert any("contradiction" in f.message and "written" in f.message for f in findings)
    assert findings[0].severity == "concern"

    # A consistent doc produces no findings.
    consistent = "+1. SAFE read-only — PASS, 5 semantic hits.\n+- `case-safe/note.md` — vault note written by the SAFE read-only case\n"
    assert scan_doc_contradictions(consistent) == []


def test_factory_dogfood_reviewer_catches_doc_contradiction(repo, tmp_path) -> None:
    """Through the REAL pipeline (patch builder, real worktree, real diff
    computation), a reviewer that reads the whole change must see the seeded
    contradiction in the final section of a doc and BLOCK it — proving the
    fix reaches the live path, not just the unit level."""
    from msb_v3.moie import build_diverse_reviewer_panel

    # A patch that writes a doc whose LAST section contradicts its earlier
    # case-1 record — the live dogfood failure shape.
    script = tmp_path / "doc_patch.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'cd "$MSB_WORKTREE"\n'
        "mkdir -p docs\n"
        "cat > docs/run.md << 'EOF'\n"
        "# Core loop\n\n"
        "## Verdict cases\n\n"
        "1. SAFE read-only — PASS, 5 semantic hits, no file written.\n\n"
        "## Artifacts\n\n"
        "- `case-safe/note.md` — vault note written by the SAFE read-only case\n"
        "EOF\n"
    )
    script.chmod(0o755)

    panel = build_diverse_reviewer_panel(
        builder_model="patch",
        models=["qwen3:8b"],
        client_factory=_contradiction_client_factory(verdict="BLOCK"),
    )
    run = _run(
        SoftwareFactory(builder=PatchBuilder(str(script)), reviewer_panel=panel),
        Issue(title="Document the core-loop run"),
        str(repo),
    )
    assert run.verdict == "BLOCKED", run.error
    assert run.review is not None
    assert any("contradiction" in f.message.lower() for f in run.review.findings)
