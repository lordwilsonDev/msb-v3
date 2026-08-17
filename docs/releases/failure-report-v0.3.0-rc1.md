# Failure & Recovery Report — v0.3.0-rc1

**Dated:** 2026-08-17 · **Scope:** the canonical live path under deliberate
failure. The pass condition is not "nothing fails" — it is that failure is
**bounded, visible, and recoverable or safely terminal** (convergence-to-12,
M5).

## Failure matrix — 11/11 modes (tests/chaos/test_failure_matrix.py)

| # | Failure mode | Expected terminal behavior | Observed |
|---|---|---|---|
| 1 | Model unavailable | bounded retry → safe halt, no uncontrolled execution | PASS — fail-closed |
| 2 | Malformed model output | validation rejects, no unverified state | PASS |
| 3 | Tool timeout | bounded, recorded, no silent continuation | PASS |
| 4 | Retry exhaustion | terminal after cap, evidence records retries | PASS |
| 5 | Permission denial | DENY, no action, denial audited | PASS |
| 6 | Duplicate request | idempotent handling, no duplicate mutation | PASS |
| 7 | Partial completion | state marked incomplete, recovery path defined | PASS |
| 8 | Stale evidence | verification fails closed | PASS |
| 9 | Restart during execution | replay reconstructs state, no guess | PASS |
| 10 | Prompt injection | content gated (untrusted → review/deny) | PASS — live-proven in case-tainted |
| 11 | Corrupted/unavailable storage | backup/restore path, chain verification | PASS — restore drill |

**Result:** 11/11 green, no unresolved P0/P1.

## Bypass invariants — 13/13 (tests/governance/test_bypass.py)

Every alternate caller / direct-invocation path that could route around the
Guard is pinned: direct tool invocation, alternate routers, unauthenticated
writes, wrong-token access, malformed payloads. 13/13 green — the governance
gate cannot be bypassed through a side door.

## Live-proven denials (artifacts/core-loop/)

| Case | What happened | Evidence |
|---|---|---|
| Tainted write (untrusted content) | `GateReview` — 3× POLICY_CHECKED → DENIED, zero files written | `case-tainted/response.txt`, `audit.json` (22 records) |
| Kill switch armed | `GateBlocked` — loop paused before any mutation | `case-kill/response.txt`, `audit.json` (18 records) |

Replay of both denied runs is `consistent: true, legal: true` with
`derived_state == stored_state == FAILED` — the denial itself is durable and
reconstructible, not just successes.

## Recovery evidence

| Recovery | Outcome |
|---|---|
| Replay engine (event-sourced reconstruction) | consistent + legal for PASS and FAIL runs (12/18/14 events) |
| Backup (launchd daily 03:00) | manual run backed up 19 DBs, notarized |
| Restore drill over corrupted runtime | 19 DBs restored, chain `valid: True` (9,858 records), partial-write file replaced |

## Known residual risks (accepted for RC, tracked)

1. Frontier seam untested end-to-end live (opt-in test only) — a frontier
   outage mid-run is therefore unproven (mitigated by local-first default).
2. p50/p95 latency sample size is small (single-run); the measurement
   mechanism is proven, the 30-day trial grows the sample.
3. Factory test-stage timeout in the worktree forced docs-only MERGE during
   the dogfood; a code change still requires full suite evidence (fail-closed
   preserved by design).
