# Supervisor restart war on :8766 — 2026-10-11

**Status:** fixed and verified live. Open flaws are listed at the bottom; this
document is the record required by the workspace constitution's redress rule.

## What was observed

Vault retrieval was not stale — it was **withheld**. Every vault-first
check returned zero results while the on-disk vault held 11,352 `.md` files.

| Probe | Before the fix |
|---|---|
| `vault-check.py --fresh` | exit **1** — `LEGACY_UNVERIFIED`, currentness `unavailable ("no source manifest")` |
| `vault-check.py "<query>"` | **zero results**, exit 1 |
| raw `POST /rag/search` | **HTTP 409** `legacy_unverified` — *"no verified source snapshot; legacy retrieval is withheld"* |
| `/health` sampled over 200s | alternating `200`/`000` — the service was up ~50% of the time |

"Nothing found" and "retrieval is broken" look identical from the outside, which
is why this read as a stale index rather than an outage.

## Root cause

Two supervisor loops were running `scripts/run.sh` against the same single-process
server on port 8766:

- **launchd** `com.lordwilson.msb-v3` (`KeepAlive`), PID 1209, since Oct 8 05:11
- **tmux** session `msb-v3`, PID 17818, since Oct 10 06:15:07 — created by
  `factory_gate_daily.sh` when the daily gate found the server down

Both loops call `reclaim_stale_port()` (`scripts/lib/reclaim-port.sh`), which kills
any port-holder whose command line looks like `msb_v3`, on the documented
assumption that it is an **unsupervised orphan**. With two live supervisors that
assumption is false: each killed the *other's* live child.

Evidence: **7,226 `reclaiming` / 7,268 `restarting in 2s`** lines in
`logs/gateway.out.log`; app log `[msb-v3] stopped` (`__main__.py:91`, SIGTERM)
then `process exited 1`. 7,268 restarts ÷ 29.0h = a **14.3s** cycle, which
independently pins the onset to the tmux session's creation time.

## Why it went unnoticed

The gate's readiness check was a **single** `curl -s -m 2 /health`. A service
cycling every 14s answers any single probe ~50% of the time, so the gate
reported *"server already up"* — a false positive that masked a half-down
service. On days the probe missed, the gate started the rival tmux supervisor
that caused the war.

## Fix applied

1. **`scripts/run.sh`** — single-instance supervisor lock (`data/supervisor.lock`,
   git-ignored). `mkdir` is the atomic primitive (macOS ships no `flock(1)`); the
   pid file inside distinguishes a live owner from a SIGKILL leftover. A second
   supervisor **waits** instead of starting a child, so it never calls
   `reclaim_stale_port()` and never kills anything. The lock's precondition
   removal is what makes the orphan assumption true again.
2. **`scripts/factory_gate_daily.sh`** — when the server is down it now
   `launchctl kickstart -k`s the existing launchd supervisor instead of spawning a
   rival under tmux (tmux remains only as the fallback when launchd does not have
   the job loaded). The readiness probe is now **two samples 1s apart**.
   `BASE_URL` and `LOG` gained `MSB_BASE_URL` / `MSB_GATE_LOG` overrides so this
   path is testable without touching the live service or the real log.
3. **`scripts/test-ops.sh`** — 7 new assertions: the lock (standby, failover,
   stale-lock clearing, release on exit) and the gate's start path (healthy →
   starts nothing; down + loaded → kickstart, no tmux; down + unloaded → tmux).

## Verification

| Check | Result |
|---|---|
| Restart war ended | counter frozen at 7,313 across the whole session |
| `vault-check.py --fresh` | exit **0** — `VERIFIED_CURRENT`, snapshot `6543d59c…` |
| Point integrity | `expected_points == points_count == 10894` |
| **Coverage, independent** | on-disk indexable files = **4,262**; snapshot `source_count` = **4,262** — exact match |
| **Retrieval end-to-end** | returned `99_Meta/research-digest-2026-10-11.md` (today's note) |
| Restart via `launchctl kickstart` | PID 1209 → 90756, health 200 in ~1s, **zero** counter increase |
| Live rival test | a real second supervisor logged `standing by for the lock`, never started a child, never touched the listener |
| `scripts/test-ops.sh` | **55 passed, 0 failed**, exit 0 |
| Mutation test | new assertions **fail** against the pre-fix scripts from `git HEAD` — the pre-fix gate's recorded call is `tmux new-session -d -s msb-v3 … bash scripts/run.sh` |

## Flaw register

### Fixed

- Mutual-kill restart war (lock + gate kickstart).
- Single-sample readiness probe that masked a cycling service (now 2 samples).
- `vault-check.py --reindex` advertised `/tmp/vault-reindex5.log`, which **nothing
  ever wrote**, and returned 0 on dispatch regardless of whether the tmux session
  started. Now the command tees to that log and a failed dispatch exits 2.
  (`vault-check.py` lives in `~/bin`, i.e. **not** in this repo — fixed in place,
  not committed here.)
- **The gate reported every FAIL as a crash.** `run_factory`'s documented
  contract (its own header, line 32) is *exit 0 when the gate is PASS or
  BLOCKED-with-no-fail; exit 1 on any FAIL* — so exit 1 was a **verdict**, but
  `factory_gate_daily.sh` treated any non-zero exit as a crash and returned
  **before** reading the verdict. Two silent consequences: the `gate_run` line
  in `daily_gate_events.jsonl` was never written (the record stops dead at
  2026-10-09, 52 entries) and the FAIL-evidence commit below it was never
  reached, so a failing gate left no committed trace and sent a misleading
  "Factory gate crashed" notification. The script now only calls it a crash when
  the exit is non-zero **and** no verdict was written. Covered by a new
  `test-ops.sh` case, mutation-checked against the gate at `HEAD`.
- The script's header documented exit code `0` for BLOCKED-with-no-fail, which
  the code never did (it exits 1 on any non-PASS verdict). Header corrected.
- **`start.sh`'s supervisor check was absolute-path only.** It used
  `pgrep -f 'msb-v3/scripts/run.sh'`, which matches only the invocations
  `start.sh` itself makes, so a supervisor started as `bash scripts/run.sh` from
  the repo root read as **not running** — the same blind spot that lets a second
  supervisor start. Replaced with `scripts/lib/supervisor-detect.sh`: a relative
  needle plus a cwd check, unit-tested in the ops suite. Mutation-checked — the
  old check answers "not-running" over a live supervisor.

  **Correction (redress) for an earlier version of this list:** I originally
  wrote that `hygiene/h02_restart_runner.py`'s `pgrep -f 'scripts/run.sh'` could
  be satisfied by another directory's copy and that its comment's claim was
  false. That was **wrong** — `h02` pairs the relative needle with an
  `lsof ... -d cwd` check and `Path(...).resolve()`, which does prevent an
  unrelated checkout from satisfying the guard. `h02` was correct; only
  `start.sh` had the defect. The new shared detector follows `h02`'s approach.
- **The gate's raw-output file was named like JSON but is not JSON.**
  `run_factory` interleaves `[run_factory] ...` progress lines with its JSON
  body, so `json.load('/tmp/factory_gate_daily_run.json')` fails, yet the error
  path sent operators there as if it were the artifact. Renamed to
  `/tmp/factory_gate_daily_run.log` (overridable via `MSB_GATE_STDOUT`) and the
  message now names both the raw output and the real artifact,
  `artifacts/hygiene/factory_gate.json`.
- **The intermittent `vault-check.py --fresh` exit 2 is root-caused.** It was
  listed here twice as an unexplained transient; it is a **client-side timeout
  misreported as a server verdict**. `freshness()` called `/rag/index/status`
  with a 10s bound, and that endpoint measured **6.5s / 12.8s / 28.3s / 8.2s**
  on four consecutive calls — every one of them returning `VERIFIED_CURRENT` —
  so 3 of 4 calls failed the bound and were reported as
  `index_status: "BLOCKED"`, which reads as "the server blocked the index". It
  never was. The bound is now 60s with one retry, a transport failure gets its
  own `STATUS_UNAVAILABLE` status so `BLOCKED` keeps its meaning, and the
  message says plainly that a slow status endpoint is not a stale index.
  Verified: 8/8 `--fresh` runs exit 0 / `VERIFIED_CURRENT`, and a genuinely
  unreachable service still exits 2 with the real reason.

  **Correction (redress):** my first hypothesis was that the *health* probe was
  timing out on a busy app, and I recorded that. Measured, it was wrong —
  `/health` answers in 0.006-0.3s, even under concurrent search. The slow
  endpoint is `/rag/index/status`. I did separately measure one 29.7s `/health`
  sample during heavy concurrent search; that stall is real, but it is not what
  produced the exit 2.
- **Lesson recorded, not just fixed:** the first version of the detector
  silently returned "not running" because bash's `pwd` is *logical* — it returns
  `/tmp/...` while `lsof` reports the physical `/private/tmp/...`, so the two can
  never compare equal. Both sides now use `pwd -P`. This is the class of bug
  that makes a liveness check quietly useless rather than loudly broken, and it
  was caught only because the new test asserted the positive case.

### Open

1. **`scripts/start.sh`'s standby path is unverified against the live service.**
   It now blocks on the lock instead of reclaiming the port. Both branches were
   reasoned through (bootstrap-succeeds; healthy port still served), but testing
   it means stopping production, so it has **not** been exercised end-to-end.
   This is the least-verified interaction in the change.
2. **TERM defers lock release** until the loop's foreground child (`python -m
   msb_v3`) exits, because bash runs traps only after a foreground command
   returns. Deliberate: running the child in the background would break
   `reclaim-port.sh`'s documented foreground-child assumption. Consequence:
   failover is correct but not instantaneous.
3. **The in-factory pytest invocation fails tests that a plain run passes.**
   The 2026-10-11 run recorded `5 failed, 4010 passed` in-factory while the same
   day's pre-push portability gate ran a clean `3939 passed, 0 failed` from a
   foreign checkout. Different counts imply a different marker set or
   invocation, and those 5 failures are the actual reason the gate reads FAILED.
   Not diagnosed. The earlier handoff note (`12_MSB/msb-v3-freebuff-handoff-2026-09-16.md`)
   records the same class of discrepancy at 3 failures.
4. **Identity-shadow criterion 1 reported "MET" from 32 stale records.** Now
   root-caused as a **symptom of this incident**: the 2026-10-11 06:15 probe died
   with `POST http://127.0.0.1:8766/mcp/proxy failed: [Errno 61] Connection
   refused` — it landed in a down-window of the 14.3s restart cycle, so no
   runtime record was written while the criterion kept reporting MET off older
   records. That is a measurement-integrity flaw in its own right: the criterion
   cannot distinguish "no evidence this run" from "the evidence is stale", and
   reports MET either way. The war is fixed, so the probe should record again on
   the next 06:15 run — **not yet confirmed**.
5. **The audit-chain anchor still reads STALE.** Not investigated.
6. **Local `main` cannot be pushed as-is.** It is ahead of `origin/main` with
   **11 of its commits marked `[do-not-push]`** (BLOCKED gate evidence,
   2026-09-28 → 10-09), and this repo's `.git/hooks/pre-push` has **no**
   `[do-not-push]` guard — the guard that exists in the PZS repo
   (`scripts/lib/held_commits.sh`) is not present here. A naive `git push` would
   publish deliberately-held evidence. The fix was therefore pushed on a branch
   based on `origin/main`, not on `main`.
7. **The pre-push hook gates the working tree, not the commits**, and this
    working tree carries 48 unrelated modified files — so any push from this
    checkout is entangled with edits that have nothing to do with it.
8. **The record layer gates docs counts.** `scripts/doc_records.py` cross-checks
    a claimed "Markdown under `docs/`: N files / M lines" in
    `docs/what-msb-v3-is.md` against the live tree, so adding *any* record changes
    those numbers and blocks a push until the claim is updated. Updating the claim
    is part of adding a record — not a bypass, and not a reason to reach for
    `MSB_SKIP_PORTABILITY=1`.
9. **Local `main`'s copy of `docs/what-msb-v3-is.md` is dirty** with a different
    set of numbers (155 files / 37,218 lines, dated 2026-09-26) that already
    disagrees with the live tree, so the lint gate fails on `main` independently
    of this change.
10. **`scripts/test-ops.sh` is red on `origin/main`, green on local `main`.**
    Run on a clean `origin/main` worktree (none of this session's changes
    present): `47 passed, 1 failed` — `ops-audit: success path wrong: rc=1`,
    expecting rc=0. The identical run on local `main` is green, so whatever
    fixes it is among the 11 held commits. The pushed branch therefore inherits
    that single failure (measured: 60 passed / 1 failed — its 13 new assertions
    all pass, 47 + 13 = 60). Worth knowing: the ops suite is **not** part of the
    pre-push gate (that runs lint + the pytest portability suite), so a red ops
    suite does not block a push and stays invisible.

11. **The status endpoint is slow on every call (6-28s, measured).**
    `/rag/index/status` re-checks the committed manifest against the source
    tree each time, so it cannot be made fast by retrying. This is the
    underlying cause of the false `exit 2` above; the client-side bound only
    treats the symptom. Unfixed — it needs caching or a cheap fast-path.