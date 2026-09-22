# MSB-v3 Research Plan — Authority–Intelligence Separation

**Written:** 2026-09-20 (Claude, coordinator) · **Status:** PROPOSED — Wilson (Evidence Authority) approves scope and flips every gate. Nothing here is a verdict.
**Governing principle:** the model proposes; the experiment decides. A refuted hypothesis is a successful outcome.
**Spec of record:** the Doctoral Research Transition Blueprint (Vault `10_Projects/msb-v3/MSB-v3-Doctoral-Research-Blueprint.md`). **Live board:** `ai-workspace/RESEARCH-STATUS.md` (gitignored — not versioned; this file and `research/baseline/` are the versioned record).

## 1. The question
Does explicit separation of AI proposal, authorization, execution, verification and evidence reduce unsafe or unverifiable autonomous behaviour while preserving useful task completion? (RQ, research blueprint §3.) Hypotheses H1 Safety · H2 Verifiability · H3 Reliability · H4 Recovery · H5 Generality · H6 Cost. **This plan attacks H1 first, alone.** (Not to be confused with audit finding "H4" about `.env` in the hardening audit.)

## 2. Where we are (verified 2026-09-20)
| Deliverable | State | Where |
|---|---|---|
| 01 R0 baseline | **Candidate + addendum closing the review's gaps** — awaiting Wilson's freeze | `research/baseline/` (original copy + `R0-ADDENDUM.md` + evidence + checksums) |
| 02 Kernel spec | Draft v0.1; Hermes critique queued (`JOB-028`, blocked); 6 open questions with proposed answers | Vault `Del02-Open-Questions-PROPOSED.md` |
| 03 Benchmark | Not started; proposed brief drafted, deliberately unqueued | `ai-workspace/job-board/drafts/` |
| 04 Baselines | Not started | — |
| 05 Protocol | Not started | — |
Post-R0 engineering already landed (identity shadow, redaction, wire-shape fix, startup guard) is **not part of R0** and is labelled post-R0 everywhere. No enforcement is on; c1 MET, c2 judgement, c3 insufficient data, c4 NOT MET.

## 3. Rules (from the blueprint; restated because they bind every phase)
One hypothesis, one experiment at a time · failure conditions written **before** running · UNKNOWN never becomes GREEN · no expansion of MSB-v3 (research blueprint §2/§25: no UI, SaaS, feature work, extra orchestration) · frozen artifacts are never edited — new versions only · any critical RED → stop, document, park, repair, retest · discipline scales to task size.

## 4. Phases (each = one worker job; the next starts only after Wilson signs the previous)
| Phase | Job / artifact | Owner | Entry | Exit (all must hold) | Stop / failure condition |
|---|---|---|---|---|---|
| **P0 Close R0** | Wilson reviews `research/baseline/`; freeze at `7cb2f7f` or list re-runs | Wilson | addendum exists ✔ | freeze decision recorded; later work labelled post-R0 | If Wilson rejects a claim, fix in a new addendum, not in `original/` |
| **P1 Kernel spec (D02)** | Hermes critique (`JOB-028`) → Wilson answers §15 → amend v0.2 → sign | Hermes → Wilson → Claude | P0 | critique written; six questions answered; spec signed; §13 formal target restated | Critique finds an unfalsifiable invariant → amend or drop it; do not proceed with it in the benchmark |
| **P2 Benchmark v1 (D03)** | `research/benchmark-v1/` tasks + attack corpus, expected outcomes stated first, checksummed **before** any run | FreeBuff (brief by Claude) | D02 signed; board decision 12 (token budget vs timeout) settled *or* timing marked contaminated | every item has expected outcome + failure condition; a second reader can score it; Wilson has read it | Any item whose expected result cannot be falsified is removed, not "interpreted" |
| **P3 Baselines (D04)** | A = direct agent (no separation) · B = simpler governed · C = MSB-v3 governed | FreeBuff | D03 frozen | A/B/C run the same benchmark unmodified; results for R0 code (`7cb2f7f`) and HEAD kept in separate columns | Any change to MSB-v3 source to "make C win" → stop (that is tuning to the answer) |
| **P4 Protocol (D05)** | hypotheses, metrics, stats, success/failure criteria, stopping rules | Claude drafts → Wilson signs | D03, D04 | frozen before any measured run; includes what result would REFUTE H1 | Protocol edited after first measured run → invalidates the run, restart |
| **P5 Experiment (post D01–05 green)** | one H1 experiment, then attack, ablation, replication, formal check (2,880-config target), multi-model, independent review | — | Wilson greenlights 01–05 | per research blueprint §24 | **Not scheduled.** No D06 until 01–05 are green (research blueprint §27) |

## 5. Decisions only Wilson can make, and when they bite
- **Now (P0):** freeze R0 at `7cb2f7f`? (recommended yes.)
- **Before P1 closes:** the six Del-02 questions (proposed answers in the vault note) and the scope tension (post-R0 hardening — accept as R0-adjacent, or stop).
- **Before P2 runs:** board decision 12 (`/chat` token budget vs read timeout) — otherwise baseline timing is contaminated; 2 (RISK_TIERS relaxation — recommended: leave fail-closed); 3 (identity registration — a grant, defer until Q2 is answered); 16 (mandatory secrets per deployment shape).
- **Parked (do not gate the experiment):** bridge read path (10), heartbeat semantics (11), TypeSafe key (14), provider reads via broker (15).

## 6. Risks to the science, named
1. **Self-grading:** every current number comes from the system's own tests, run by AI agents. Independent scrutiny is a research blueprint §24 gate; nothing before it should be called "verified" in a paper.
2. **Contaminated measurement:** the running service now differs from R0 (post-R0 shadow wiring, redaction, timeouts). Measure R0 from an isolated checkout (`git worktree`), not the live service.
3. **Tuning to the answer:** benchmark frozen and checksummed before baselines run; the two must never be edited together.
4. **Tier tests kill the live service** (`test_cold_state_verification.py` SIGKILLs :8766). They run only in a deliberate, isolated tier run.
5. **Coordination:** the board is gitignored, and another session commits to this repo concurrently — always re-read `git log` and `git stash list` before acting; never rewrite pushed history.
6. **Scope drift:** the lane has already crossed into engineering once (§2 tension). Any new job must answer the seven questions in research blueprint §2 or it does not enter the lane.

## 7. Queue (in order; nothing runs until the entry column is satisfied)
1. P0 — Wilson freeze decision.
2. `JOB-028` Hermes critique (in `blocked/`; must be started by hand — nothing auto-polls the board).
3. Wilson answers Del-02 §15 → Claude issues D02 v0.2 → Wilson signs.
4. D03 brief moved from `drafts/` to `ready/` as **one** job.
