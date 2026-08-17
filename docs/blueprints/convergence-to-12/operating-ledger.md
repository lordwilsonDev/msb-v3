# MSB v3 — Operating Ledger (M6 personal production trial)

**Owner:** Wilson · **Opened:** 2026-08-16 · **Purpose:** record real usage so value is *measured*, not claimed. The M6 exit criterion is ≥30 days of real tasks with a measurable baseline comparison.

> Rule: record failures and manual bypasses as carefully as wins — they are the most valuable rows.

## How to fill one row
- One row per real task run through MSB (or attempted).
- **Baseline time** = honest estimate of doing it by hand (the old way).
- **MSB time** = wall-clock including supervision.
- **Intervention** = what a human had to do (approve, fix, retry, bypass).
- **Evidence used** = whether the audit/evidence record was actually consulted or useful.
- **Outcome quality** = 1–5 vs. what the baseline would have produced.

## Ledger

| Date | Task type | Baseline time | MSB time | Intervention (Y/N + what) | Outcome quality (1–5) | Failure mode (if any) | Evidence record useful? | Notes |
|---|---|---|---|---|---|---|---|---|
| 2026-08-16 | — (seed row: governed handle loop demo) | 30m | 15m | N | 4 | — | Y | `fb0b15ed6c48aedb` PASS run — plan→gate→execute→verify→evidence |
| | | | | | | | | |
| | | | | | | | | |

## Monthly rollup (recalculate every ~30 days)

| Metric | Value | vs. baseline |
|---|---|---|
| Tasks completed | | |
| Tasks needing human intervention | | |
| Interventions categorized (approve / fix / retry / bypass) | | |
| Median time saved per task | | |
| Evidence record useful on N tasks | | |
| Release driven by operating data (what changed?) | | |

## M6 decision gate
> Continue toward external users only if the system produces repeatable value **without becoming more work to supervise than the work it replaces.**
