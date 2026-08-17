# MSB v3 — M6 Personal Production Trial (30 days)

**Owner:** Wilson · **Start:** 2026-08-17 · **End:** 2026-09-16 (rolling)
· **Gate:** positive net value after supervision, or narrow the workflow.

## The workflow (one recurring workflow, used repeatedly — not changed daily)

**Vault research-to-note.** A real question about the vault's content,
resolved to a written note through the governed path:

```
request → intent → task DAG → ActionGate → search/read tools
       → synthesis → (write with approval) → verification → evidence
```

**Why this one:** it is the proven canonical path (M1 decision; run
`dbb-20260817T015727-07143` PASS), it exercises reasoning + retrieval + a
controlled mutation + verification + replay, and it maps to real personal
work (vault notes are the deliverable). The deliberately rejected
alternative was the daily research digest (no consequential tool actions to
govern).

### Task template (use this shape for every trial task)

> "Search the vault for recent decisions about **<TOPIC>** and write a
> one-page note summarizing them to **<PATH>**."

Variants allowed (read-only version drops the write clause), but the shape
stays fixed so the ledger compares like with like.

## Logging every task

Two ways, both append to `operating-ledger-entries.md`:

1. **One command (recommended):** run the task through the helper, which
   captures run_id + verdict + hash + duration and appends the entry:

   ```bash
   bash scripts/trial-log.sh "Search the vault for ... " "path/to/note.md"
   ```

2. **Manual:** copy the Entry template at the top of
   `operating-ledger-entries.md` and fill the fields.

Rule: log **every** real task, wins and failures alike. Failures and manual
bypasses are the most valuable rows.

## Weekly review cadence (every Friday)

| Step | What | Evidence |
|---|---|---|
| 1–3 | Count tasks, completion rate, interventions by class, median MSB vs baseline | `python3 scripts/trial-rollup.py` (parses the ledger; `--json` for machine-readable) |
| 4 | Re-run the run-report for latency/verdicts/retries | `python3 scripts/run-report.py` |
| 5 | Pick ONE keep/change/cut decision from the week's data | record in MILESTONES.md M6 row |

The rollup is covered by hermetic parser tests
(`tests/ops/test_trial_rollup.py`) covering both ledger formats (the
`trial-log.sh` shape and the manual template), so a format drift breaks
a test, not a review.

At the end of each week, one sentence in MILESTONES.md M6: what the data
said, and the one decision made. If supervision cost > time saved two weeks
running, narrow the workflow (drop the write variant, or restrict topics).

## Monthly rollup (day 30)

Fill the rollup table in `operating-ledger.md`:
tasks completed, intervention rate, categorized interventions, median time
saved, evidence usefulness count, and the release driven by operating data.

## M6 decision gate

> Continue toward independent-user validation (M7) only if the system
> produces repeatable value **without becoming more work to supervise than
> the work it replaces.**

If the gate fails: narrow the workflow and re-run the trial on the narrowed
scope before any M7 scheduling.
