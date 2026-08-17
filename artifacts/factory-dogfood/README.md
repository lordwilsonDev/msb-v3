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

| Stage | Run 1 (qwen2.5-coder:0.5b) | Run 2 (qwen3:8b) | Run 3+4 (qwen3:8b, plumbing fix) | Run 5 (qwen3:8b, +deterministic scan) |
| --- | --- | --- | --- | --- |
| classify | medium | medium | medium | medium |
| plan | ok | ok | ok | ok |
| build | ok | ok | ok | ok |
| test | failed (timeout) | failed (timeout) | failed (timeout) | failed (timeout) |
| review | APPROVE — MISSED | APPROVE — MISSED | APPROVE — MISSED | **CONCERN — CAUGHT** |
| verdict | NEEDS_WORK | NEEDS_WORK | NEEDS_WORK | NEEDS_WORK |

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

Third fix (run 5): a **deterministic coherence scan** (`scan_doc_contradictions`
in `src/msb_v3/factory/reviewer.py`) now runs on EVERY review, independent
of which MoIE controller is configured. It flags a verb that appears both
asserted and negated in the change text ("no file written" vs "vault note
written"). A weak LLM approving a self-contradictory change can no longer
be the only guard.

## Docs-only merge proof (2026-08-17, run 6/7)

A docs-only change (`.md` under `docs/`) now skips the full test suite with
an explicit classified-skip reason (recorded in the evidence chain, distinct
from the honest UNVERIFIED of `ran=False`) and can reach **MERGED**. Run 7:
verdict **MERGED**, test skipped (never ran), review APPROVE, verification
PASS — in **24s** instead of the ~328s test-stage timeout that blocked every
earlier run.

The coherence scan was also narrowed to unambiguous completed-action verb
forms (written/created/deleted/ran/...): the clean doc's "Canonical run"
(noun) next to "without running tests" (gerund) was a false positive on the
bare form "run", now covered by a regression test.

## Honest findings

1. **Fail-closed works.** No code change merges without real test evidence;
   a docs-only classified skip is recorded evidence with a reason, not an
   absence of evidence.
2. **Runs 3-4 proved the mechanism; run 5 proved the catch.** With the
   full diff + coherence lens, qwen3:8b still approved (model judgment is
   the weak link). The deterministic scan is the safety net: run 5's review
   verdict is **CONCERN** ("both asserts and negates 'write'/'written'")
   even though the model approved. The contradiction can no longer pass,
   whatever the reviewer model.
3. **Regression tests pin the fix:** `test_reviewer_prompt_carries_full_diff_tail`,
   `test_factory_dogfood_reviewer_catches_doc_contradiction`,
   `test_compute_changes_emits_diff_for_new_file`,
   `test_single_model_panel_is_coherence_reviewer`,
   `test_scan_doc_contradictions_flags_assert_and_negate` (incl. the
   noun/gerund false-positive case), the decisive
   `test_deterministic_scan_catches_contradiction_even_when_llm_approves`,
   `test_is_docs_only_change_classifies_extensions_dirs_and_filenames`, and
   `test_factory_merges_docs_only_change_without_running_suite`.
