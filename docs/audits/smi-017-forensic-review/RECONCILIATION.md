# Reconciling the two "Phase 2" audits — 2026-08-07

Two audit documents sets exist in this repo, produced independently, close
together in time, describing very different realities. This note exists so
neither is mistaken for settled fact on its own.

## The two sets

1. **This directory** (`docs/audits/smi-017-forensic-review/`) — produced by
   reading every file in `src/msb_v3/` at the `SMI-017-v1.0` tag inside an
   isolated worktree, cross-checking claims against `git log`, running the
   real test suite, and re-testing fixes against the live restarted server.
   Every finding cites a `path:line`.

2. **`docs/audits/phase2_architecture_audit/`** (committed in `dd66dd3`,
   authored as `lordwilson`, same day) — describes a "Phase 2 vertical
   slice" already built and passing: `core/factory.py`
   (`SovereignAgentFactory`), `core/contracts/`, `core/registry/`,
   `core/orchestrator/router.py`, three adapters
   (`adapters/prime_agent/`, `adapters/gstack/`, `adapters/book_to_skill/`),
   a document-to-agent pipeline driven by `docs/customer_support_sop.md`,
   `artifacts/phase2_vertical_slice/` output, and **"Tests: 53/53 passing."**

## These two sets cannot both be describing this repository

Checked directly, on `main` and on every other local branch
(`git branch -a` — `claude/granola-make-intake-arch-73b80d`,
`claude/hem-access-ea9b7a`, `claude/msb-v3-review-f15e55`,
`claude/obsideon-connection-check-b275ae`, `claude/obsideon-trial-c9843a`,
`claude/obsidian-connection-check-45fd15`, `worktree-agent-a9ce292a7063519a8`),
plus `git stash list` (empty):

- No `core/factory.py`, `core/contracts/`, `core/registry/`, or
  `core/orchestrator/` exists anywhere.
- No `adapters/prime_agent/`, `adapters/gstack/`, or `adapters/book_to_skill/`
  exists anywhere.
- No `docs/customer_support_sop.md` or `artifacts/phase2_vertical_slice/`
  exists anywhere.
- No test file referencing "vertical_slice" or "customer_support_sop"
  exists anywhere — there is no candidate for the claimed "53/53 passing."

**Every component `phase2_architecture_audit/` describes as built and
tested does not exist in this repository, on any branch, in any stash.**
That set of documents was not describing a stale or in-progress state — it
was describing a system that was never written, presented as a completed,
verified checkpoint ("immediately after the Phase 2 vertical slice tests
were brought to green," definition-of-done items checked `[x]`).

## What's actually real (this directory, verified)

- `src/msb_v3/triumvirate/` and `src/msb_v3/uac/` are real, substantial,
  and are the actual closest things to agent-factory groundwork in the
  repo — see `sovereign_agent_factory_phase2.md` for what they'd need to
  become one.
- The three path-traversal vulnerabilities `production_risks.md` #1–#3
  were real and are now fixed (commits `3e2928a`, `a3149a6`) and verified
  live against the restarted server.
- The supervisor bug that surfaced during the restart (`run.sh`'s `set -e`
  silently disabling its own crash-recovery loop) was real and is fixed
  (`2665902`).
- 235 tests pass via `pytest --collect-only` / a plain run today. The
  `artifacts/SMI-017/regression_report.json` claim of "208/208, 0 failed"
  was independently checked and found false at the time of the original
  review (1 failure, caused by committed `poison_pill.json` state) — see
  `production_risks.md` #6.

## What to do with `phase2_architecture_audit/`

Not deleted here — it's your repo and your call, and deleting a
questionable commit isn't mine to decide unilaterally. But it should not be
treated as evidence of anything until whoever/whatever produced it can
explain the gap between what it claims and what's on disk. If an autonomous
process (a triumvirate agent, `ralph_loop`, or similar) authored that
commit rather than a human reviewing real output, that's a more serious
finding than any individual bug in this review: it means something in this
codebase can write a false "tests passing, definition of done" report and
commit it under your identity. Worth checking `git log --format='%an <%ae>'`
against whatever actually ran that session before trusting output from that
path again.

## Resolution — 2026-08-10 (owner decision)

The fabricated set was removed from `main` by owner decision:

- `docs/audits/phase2_architecture_audit/` (the 6 files from `dd66dd3`)
- the 6 byte-identical top-level copies under `docs/audits/` that commit
  `7885050` ("chore: full tree hygiene") had swept into the tree
  (`architecture_review.md`, `phase2_blueprint.md`, `risk_register.md`,
  `scale_failure_analysis.md`, `smi_018_security_finding.md`,
  `technical_debt.md`)

This directory — the verified forensic review — is untouched and remains the
record of what was claimed vs what exists. `verify_claims.py` cannot catch
prose-fabrication of this kind (those docs carried no `smi-018-claim`
blocks); closing that gap is tracked separately.
