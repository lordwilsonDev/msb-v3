# Factory dogfood — evidence artifact (2026-08-17)

One real MSB change run through the Software Factory end-to-end on the live
stack (`/factory/run`, operator-gated), per the release-gate sequence #5.

## The change

`docs/core-loop/factory-dogfood-2026-08-17.md` — a real doc recording the
core-loop verification session (canonical run, three verdict cases,
recovery evidence). Built by the deterministic `patch` builder in an
isolated worktree (`artifacts/factory-dogfood/dogfood-patch.sh`).

**Seeded defect:** the doc's closing "Artifacts" section claims the SAFE
read-only case wrote `case-safe/note.md` to the vault — directly
contradicting the case-1 record two sections above ("no file written").
A reviewer reading the diff alone should flag the contradiction.

## Pipeline evidence (`run.json`, `run2.json`)

| Stage | Run 1 (reviewer qwen2.5-coder:0.5b) | Run 2 (reviewer qwen3:8b) | Run 3+4 (reviewer qwen3:8b, post-fix) |
| --- | --- | --- | --- |
| classify | medium | medium | medium |
| plan | ok | ok | ok |
| build | ok — 1 changed file | ok — 1 changed file | ok — 1 changed file |
| test | ran, **failed** (timeout — full suite >300s in worktree) | ran, **failed** (timeout) | ran, **failed** (timeout) |
| review | **APPROVE** — defect MISSED | **APPROVE** — defect MISSED | **APPROVE** — defect MISSED |
| verify | FAIL | FAIL | FAIL |
| verdict | **NEEDS_WORK** | **NEEDS_WORK** | **NEEDS_WORK** |

## Root cause found + fixed (2026-08-17 follow-up)

Runs 1-2 approved the seeded defect for a concrete, fixable reason: the
reviewer **never saw the change**. `compute_changes` skipped the diff for
NEW files entirely (the missing old file raised OSError and the loop
`continue`d) — a brand-new doc produced an EMPTY diff, so `build.diff` was
empty and the LLM had nothing to read. Fixed in
`src/msb_v3/factory/builders.py`: a missing side reads as `[]` instead of
skipping, so new files appear as full `+` diffs.

Second fix: `build_diverse_reviewer_panel` cycled lenses across models, so a
single-model panel only ever got the FIRST lens (security) — the coherence
lens never fired. The last model is now pinned to coherence, and the base
reviewer contract explicitly demands an internal-consistency check. The
coherence lens reads the WHOLE change (the old prompt truncated the diff to
2000 chars, hiding the tail of every doc).

## Honest findings

1. **Fail-closed works.** No run merged: missing/red test evidence →
   NEEDS_WORK, never a pass.
2. **The mechanism is now correct, but the 8B model still approved.**
   Runs 3-4 confirm the full diff reaches the reviewer and the coherence
   lens fires (single-model panel = coherence reviewer), yet qwen3:8b
   approved a doc whose final section contradicts its own case-1 record.
   The deterministic MoIE reviewer catches seeded defects (hermetic M4
   suite); the live 8B local model is the weak link — a doc-level
   contradiction needs a stronger model or the deterministic rules. This is
   recorded honestly: the pipeline no longer STARVES the reviewer, and the
   remaining gap is model judgment, not plumbing.
3. **Regression tests pin the fix:** `test_reviewer_prompt_carries_full_diff_tail`
   (the tail of a >2000-char diff reaches the prompt),
   `test_factory_dogfood_reviewer_catches_doc_contradiction` (a coherence-
   reading reviewer catches the contradiction through the real pipeline),
   `test_compute_changes_emits_diff_for_new_file` (new files produce diffs),
   `test_single_model_panel_is_coherence_reviewer`.
