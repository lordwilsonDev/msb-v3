# Core-Loop Golden Fixtures

Live fixtures captured from the running stack (Ollama `qwen3:8b` +
`nomic-embed-text`, app on `:8766`) on 2026-08-17 at commit `e3fd3ff`
(v0.3.0-rc1 baseline). These are the release-gate evidence for the
convergence-to-12 program.

## Re-running

```bash
bash scripts/start.sh start          # ensure the app is on current code
bash scripts/capture-verdict-fixtures.sh   # SAFE + tainted-DENY + kill-BLOCK
# kill-BLOCK needs the switch armed first (see below); the script captures
# the tainted-write denial for that case, so for the true kill-block run:
#   curl -X POST :8766/governance/killswitch/arm  (bearer token)
#   curl -X POST :8766/agent/handle  (a write request)   -> GateBlocked
#   curl -X POST :8766/governance/killswitch/disarm
```

## Fixtures

| Case | Verdict | Files | Evidence |
|---|---|---|---|
| `run1/` | PASS (canonical task) | `response.json`, `replay.json`, `write-the-synthesized-one.md` | 3-task DAG, 5 semantic hits, deterministic hash `30a7ccc192e0bbb6`, replay consistent/legal (15 events, COMPLETED) |
| `case-safe/` | PASS (read-only) | `response.txt`, `replay.json`, `audit.json` | 5 semantic hits, no file written, replay consistent (12 events, COMPLETED), 16 audit records |
| `case-tainted/` | FAIL (DENY) | `response.txt`, `replay.json`, `audit.json` | `GateReview: action review required: action driven by untrusted content`, 3× POLICY_CHECKED → DENIED, replay consistent (18 events, FAILED — denial states reconstruct), 22 audit records, **no file written** |
| `case-kill/` | FAIL (BLOCK) | `response.txt`, `replay.json`, `audit.json` | `GateBlocked: action blocked: kill switch armed — loop paused`, replay consistent (14 events, FAILED), 18 audit records, **no file written** |

## What "replay consistent" means

`replay.json` contains the ReplayEngine output: the event-sourced timeline
is re-derived from the audit/event records and compared against stored state.
`consistent: true` + `legal: true` + `derived_state == stored_state` means
the run's outcome can be reconstructed from its events without the model —
the recovery evidence for the release gate. Denied and blocked runs replay
to FAILED, proving the *denial itself* is durable and reconstructible, not
just successes.

## Verified negative invariants (from the audit trails)

- case-tainted: `POLICY_CHECKED → DENIED` appears for every write attempt
  (each retry re-gated, each denied) — the write never executed.
- case-kill: `GateBlocked` before any mutation — the loop paused.
- No `MUTATION_COMMITTED` record exists in either denied/blocked trail.
