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

| Stage | Run 1 (reviewer qwen2.5-coder:0.5b) | Run 2 (reviewer qwen3:8b) |
| --- | --- | --- |
| classify | medium | medium |
| plan | ok | ok |
| build | ok — 1 changed file | ok — 1 changed file |
| test | ran, **failed** (timeout — full suite >300s in worktree) | ran, **failed** (timeout) |
| review | **APPROVE** — defect MISSED | **APPROVE** — defect MISSED |
| verify | FAIL | FAIL |
| verdict | **NEEDS_WORK** | **NEEDS_WORK** |

## Honest findings

1. **Fail-closed works.** Neither run merged: missing/red test evidence →
   NEEDS_WORK, never a pass. The pipeline refused to merge on incomplete
   evidence exactly as designed.
2. **Seeded defect NOT caught by the live LLM reviewers.** The hermetic M4
   suite (`tests/factory/test_factory_dogfood.py`) proves the deterministic
   MoIE reviewer catches a seeded BLOCK; the live 0.5B/8B reviewers both
   approved a doc with an internal contradiction. Conclusion: reviewer
   strength matters — a doc-level contradiction needs a model with enough
   context reasoning, or a lens that re-reads the whole diff. This is the
   real gap the dogfood exposed, not a broken pipeline.
3. **Test stage timeout is a harness artifact.** The worktree run of the
   full suite exceeds the 300s stage budget. A docs-only change shouldn't
   need the full suite, but the factory's default is conservative — which is
   the safe direction.
