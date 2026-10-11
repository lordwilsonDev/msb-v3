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
3. **`scripts/test-ops.sh`** — 16 new assertions (48 items before → 64 now, 0
   failing): the lock (standby, failover, stale-lock clearing, release on exit),
   the gate's start path (healthy → starts nothing; down + loaded → kickstart, no
   tmux; down + unloaded → tmux), the supervisor detector (relative invocation,
   per-checkout cwd filter), and the H9 schema check (absent dir = 0 DBs, empty
   dir = same verdict, a v0 DB still fails).
4. **`scripts/stamp-schemas.py`** — `--check` no longer fails on an absent `data/`
   (see the register below): absent and empty now agree, the stamping path still
   errors on a missing dir, and genuine v0 drift still fails.
5. **`scripts/notarize_chain_anchor.sh`** — single-instance lock (item 4 of the
   register): two launchd notary jobs sharing one schedule could notarize
   concurrently; the seq race plus seq-only remote dedupe re-pushed one name
   twice and Drive turned that into same-name siblings, which the verifier read
   as one entry pasted onto itself. With the lock, racing runners skip loudly
   and the off-box notary cannot be double-pushed from this host.

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
| `scripts/test-ops.sh` | **64 passed, 0 failed**, exit 0 (48 at `origin/main`, of which 1 was failing) — measured on a clean tree, i.e. **before** any pytest run in that checkout; running pytest first creates 8 v0 DBs and the H9 step then reports 63/1, correctly (see Diagnosed 3) |
| `pytest tests/docs/` | **69 passed** (was 69 *errors* without `PYTHONPATH` pinned: a worktree imports `msb_v3` from the editable install at the main checkout, so conftest and package came from different trees) |
| Mutation test | new assertions **fail** against the pre-fix scripts from `git HEAD` / `origin/main` — the pre-fix gate's recorded call is `tmux new-session -d -s msb-v3 … bash scripts/run.sh`; the pre-fix `stamp-schemas.py --check` fails the absent-dir assertion with rc=1, and the pre-fix FAIL path writes no verdict |
| Factory pytest failures | reproduced (`5 failed, 4010 passed, 17 skipped, 77 deselected`); **4 pass, 1 skips** at a clean `origin/main` worktree — the fifth is host hook drift, not tree state |

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
- **`stamp-schemas.py --check` failed every fresh checkout, and ops-audit
  reported it as schema drift.** `data/` is gitignored, so a fresh clone or
  worktree has none. The script returned **1** whenever the data dir was
  missing, and `ops-audit.sh`'s H9 step turned *any* non-zero into `FAIL:
  unstamped SQLite DB (run scripts/stamp-schemas.py)` — a cause it never
  verified, pointing the operator at a script that cannot fix an absent
  directory. Measured on a clean `origin/main` worktree: no `data/` →
  `[stamp-schemas] data dir not found: .../data` → rc 1 → the ops-audit success
  path fails (`47 passed, 1 failed`). An **empty** dir took the opposite
  verdict (`OK: 0 DB(s)`, rc 0), which is the tell: absent and empty mean the
  same thing — no databases to stamp — and only one of them was accepted.
  `--check` now treats the absent dir as vacuously fine, while the *stamping*
  path still errors on a missing dir and a genuinely v0 DB still fails. With
  the fix the suite is `64 passed, 0 failed`; with the fix reverted the new
  absent-dir assertion fails on `rc=1` (mutation-checked), so it is not vacuous.
  This is the real reason the ops suite was red on `origin/main` (old item 10,
  now closed) and it also buried the drift signal the gate exists to carry.

  **Correction (redress):** the previous version of this register said
  *"whatever fixes it is among the 11 held commits"*. **That was wrong.**
  Nothing in the held commits touches it — `scripts/stamp-schemas.py` is
  **byte-identical** between `origin/main` and local `main` (empty `git diff`),
  and the held commits change no DB-creation code. The same code was green on
  local `main` only because that checkout has a long-lived `data/` whose DBs
  were stamped at some earlier point: **disk state, not code.**

### Diagnosed — root cause reached, no code change made

1. **The in-factory `5 failed, 4010 passed` is tree/host state, not a code
   regression.** Reproduced by running the factory's own command
   (`python -m pytest tests/ -q`) in the real checkout: `5 failed, 4010 passed,
   17 skipped, 77 deselected`. The two headline numbers match the in-factory
   figure exactly, which is what identifies the two runs as the same selection.
   **Provenance caveat, stated so nobody over-reads this:** the *factory's own*
   copy of that figure is **no longer re-readable** — `daily_gate_events.jsonl`
   holds exactly **one** 2026-10-11 event (the identity-shadow probe) and **no
   `gate_run` record at all** (suppressed by the crash bug above),
   `~/Library/Logs/msb-factory-gate.log` contains **no `pytest` line**, and the
   raw stdout artifact `/tmp/factory_gate_daily_run.log` is **0 bytes**. The
   quotation of the factory's summary is therefore carried from earlier in the
   session; my reproduction is the evidence that survives.

   **Correction (redress):** I wrote that **all five pass** at a clean
   `origin/main` worktree. Measured: **4 passed, 1 skipped**. The fifth,
   `moie/test_moie_cli.py::test_installed_hook_matches_versioned`, cannot be
   shown to pass anywhere — it **fails** on the main checkout (the installed
   `.git/hooks/pre-push` is stale against `scripts/hooks/pre-push`) and **skips**
   wherever no hook is installed. So it is **not** working-tree state like the
   other four: it is a genuine, still-open **host/deploy drift** defect, and the
   fix is `make hooks-install`, which I have deliberately not run because it
   changes the host environment. The other four are tree-state and pass
   cleanly. The failures are docs/host gates that measure the *live
   working tree*:

   | test | cause |
   |---|---|
   | `docs/test_blueprint_index.py::test_index_matches_tree` | `docs/blueprint-index.md` (modified WIP) drifted from the tree |
   | `docs/test_doc_records.py::test_gate_is_green` | 8 stale count claims in `docs/what-msb-v3-is.md` |
   | `docs/test_doc_records.py::test_counts_match_live_measurement` | the same 8 mismatches |
   | `docs/test_surface_map.py::test_surface_map_covers_every_subpackage` | untracked WIP subpackage `msb_v3/question_engineer` undocumented in `docs/SURFACE.md` |
   | `moie/test_moie_cli.py::test_installed_hook_matches_versioned` | `.git/hooks/pre-push` stale vs `scripts/hooks/pre-push` (host state; skips on a fresh clone) |

   **Why the two runs disagree at all** (this is the part the old item got
   backwards): the marker set is *identical* — **77 deselected in both**. The
   difference is the **tree**, not the invocation. The pre-push gate runs in a
   staged copy **with no `.git`**, where `tests/docs/test_doc_records.py` skips
   **9 tests** (of 32 skips) and `moie/test_moie_cli.py` skips 1; the factory
   runs in the real checkout, where they execute. So the gate that blocks a push
   is structurally **blind** to the doc-drift tests the daily gate fails on —
   the two can never agree.

   **Not fixed, deliberately:** the honest repair is a policy choice — either the
   daily gate stops judging uncommitted working-tree state, or the WIP tree gets
   its claims regenerated. Both change what the gate means, and the second one
   touches 47 files of somebody else's in-progress work.
2. **The slow `/rag/index/status` is cost, not a lock — and it is not cached.**
   Measured in-process: `_tree_manifest` **0.28s** warm / 0.82s cold (the walk
   reads **4,263 files / 23.6 MB** — *not* the 12,169 files / 179 MB I first
   recorded, which was an unexcluded `rglob` that swept `Claude-Conversations`;
   corrected here), `_verify_snapshot_collection` **1.02s** for 10,894 points in
   43 scroll pages, full endpoint **0.89s** warm. Over HTTP: **5.74s** now,
   and 6.5 / 12.8 / 28.3 / 8.2s when first measured — variance that tracks page
   cache and Qdrant latency, not server load. Chunk-level hashing is only
   **0.03s** of it, so "optimise the hashing" was never the answer.

   **Two hypotheses falsified by measurement:** (a) that a *busy* app made the
   status call slow — `/health` answered in **0.006-0.3s** even under concurrent
   search; (b) that a synchronous CPU-bound handler **starved the event loop** —
   during a **5.74s** status call, six consecutive `/health` requests returned in
   **0.00-0.03s**, so FastAPI is running the handler in its threadpool. No cache
   was added: a cached `VERIFIED_CURRENT` would re-introduce exactly the
   stale-looks-current failure this layer was hardened against, and the real fix
   is client-side, which is already in (`vault-check.py`: 60s bound + one retry,
   and `BLOCKED` no longer means "the client gave up").

3. **Running the test suite creates v0 databases that the H9 gate then flags.**
   A second, independent mechanism behind the same red ops suite. Measured on a
   clean branch checkout: `data/` absent → running ***one* test file**
   (`pytest tests/docs/test_doc_records.py`) creates **8 databases under
   `CWD/data/`, all at schema v0**; `--collect-only` creates none, and a bare
   `import msb_v3` creates none. So it is the test *body*, not import, and it
   lands in the working tree rather than `tmp_path`.

   The enabler is in `src/msb_v3/core/config.py`: several DB defaults are
   **relative** — `"data/node/node.db"`, `"data/node/audit_chain.db"`,
   `"data/vectors/vectors.db"`, `"data/memory_fabric/memory.db"`,
   `"data/evidence/decision_spine.db"`, `"data/codegraph/graph.db"` — and a
   relative path resolves against **the process CWD**, bypassing the repo
   anchor. `tests/conftest.py` only redirects the paths it explicitly patches
   (autouse fixtures pointing each at `tmp_path`); anything constructed before
   those run, or any default it does not cover, spills into the checkout's
   `data/`.

   **Consequence:** run the tests, then run the ops suite, and `ops-audit`'s
   success path fails — *correctly*, because the DBs really are unstamped. This
   is why the gate can be red on a developer machine with no code change at all,
   and it is the second face of "`test-ops.sh` is red". **Not fixed:** anchoring
   these defaults is a change to path semantics across `settings`, which is a
   wider blast radius than this incident — and deployments may be relying on
   CWD-relative resolution. Recorded so the next person does not re-derive it.

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
3. **Identity-shadow criterion 1's Oct-11 event read zero new records.** Now
   root-caused as a **symptom of this incident**: the 2026-10-11 06:15 probe
   completed its **chat** exercise — the server wrote a runtime-origin record
   at 11:15:07 UTC (`session=identity-shadow-probe`, actor `chat.agent`,
   kernel `REJECTED/unknown-actor`) — then **crashed on mcp-bridge** with
   `POST http://127.0.0.1:8766/mcp/proxy failed: [Errno 61] Connection
   refused`, landing in a down-window of the 14.3s restart cycle. Two
   consequences, both measured:

   - The step that judges the probe composes its event from the probe's JSON;
     a probe that died has none, so the event read `probe_ok=false /
     new_records=0` **even though the corpus gained a fresh actor record
     seconds before**. The zero is the probe-output delta, not a corpus miss —
     an earlier version of this entry said "no runtime record was written",
     which was wrong (corrected here).
   - That record itself is a **kernel REJECTED with `unknown actor:
     chat.agent`**: the probe faithfully exercised the surface, and the live
     server does not currently *accept* the probe's actor. Criterion 1 counts
     records that "carry an actor" — this one carries one — so the tally
     scrolls on regardless of the REJECTED kernel state. Whether a surface
     whose only actor-evidence is a rejection should count as satisfying
     "carries an actor" is the same freshness/semantics question already
     BLOCKED_APPROVAL below.

   **Earlier rounds' concern is thereby closed — the probe records again** on
   Oct-10 and Oct-11 alike (daily ~11:15 UTC, one record per successful day,
   7 runtime records ≤ 7 days as of this evening).

   **The underlying mechanism is now proven with controls, not inferred.**
   `k22_status` reads the whole corpus with **no age window** — the criterion
   judges all-time evidence. Isolated reproduction against a tmp store:
   runtime rows entirely **60 days old** (both surfaces, actors, ALLOWs) plus
   one actor-less record from today → **MET**. Two controls make it conclusive:
   fresh-and-acted → MET, and **fresh-but-actorless → NOT MET** — so the MET
   verdict can only come from the stale rows. Pinned as a documenting regression
   pair in `tests/governance/test_identity_shadow_status.py` (26 pass in that
   file; the pin exists so any future freshness window fails loudly and the
   policy change becomes deliberate rather than accidental).

   **Live corpus today is genuinely fresh, but thinly so:** 2,902 records,
   ages 0.1–21.6 days, median 18.4; **86 records ≤ 48 h but only 2 of them
   runtime-origin** (7 within 7 days — one per daily probe). So today's MET is
   honest, and the flaw is not misreporting *now* — it is the missing freshness
   semantics that made the down-window invisible and would let it stay
   invisible.

   **Deliberately not fixed — BLOCKED_APPROVAL:** changing criterion 1's verdict
   semantics (a freshness window, dated verdicts, or alerting on zero new
   records) is a governance-policy decision redefining what MET means for K22
   exit, not a bug fix. The evidence is in; the owner call is not mine.
4. **The audit-chain anchor was never STALE — root-caused as misreported remote duplicates.**
   The register entry above was **wrong**: the anchor verifies **VALID**
   (tip `9124b8fe…`, 237,928 records, signing chain intact). The exit 2 that
   made the daily `chain-anchor-verify` job red comes from a **different,
   later check in the same script**: the remote notary verdict
   `REMOTE_INVALID`.

   Root cause, measured live read-only against `gdrive:msb-v3/chain-anchor-notary`:
   the remote listing holds **125 entries for 119 unique names** — Google Drive
   permits same-name siblings. Each duplicated name reads back as the two
   objects **concatenated** (1484 = 2 × 742 bytes, halves byte-identical, each
   half equal to its local `.jsonl` line), so `json.loads` fails and the
   verifier stamped the intact content **"not JSON"** — language that reads as
   tampering. **All 5 named failures are in the duplicated set**, plus a **6th**
   name the report never showed, because the display slice `invalid[:5]`
   silently capped the list and made it look complete. The affected entries are
   2026-09-26 → 10-03 plus 10-10, and every one is byte-identical to its local
   line — so this is redundancy with a label problem, not corruption.

   Fixed in `msb_ledger/notary.py` (single module, re-exported as the
   `msb_v3.uac` compatibility namespace): the JSON-decode failure path now
   counts back-to-back JSON documents and reports
   `name resolves to N objects on the remote (duplicate push — content intact,
   name ambiguous)` when N > 1, reserving `not JSON` for a genuinely
   unparseable payload; the report now carries `invalid_count` and the detail
   line says `and N more` past the display window. Three new regressions in
   `tests/uac/test_notary.py` (duplicated-object labelling, genuine non-JSON
   still labelled, the true count past the cap) — **2 of 3 fail against the
   exact pre-fix code** (mutation-checked, mutant compiles), the third pins the
   non-swallowing invariant. `tests/uac/` overall: **199 passed**.

   **Not fixed — needs an owner decision:** the duplicates exist on the remote.
   The verifier now names them honestly; it does not delete or overwrite them
   (deleting one of a same-name pair on Drive is ambiguous, and `rclone copyto`
   cannot refresh only one half). Whether to reconcile the remote, tolerate
   counted duplicates, or move the sink to an object-lock/WORM backend is a
   retention and integrity policy choice.

   **The write-path question is now answered — it was this incident's own
   twin schedule, not a retried `copyto`.** Fingerprint chain, all measured:

   1. **Two launchd jobs share one schedule.** Both
      `com.blackswanlabz.msb-v3.notary` and `com.lordwilson.msb-chain-notary`
      are loaded, both run the *same* `notarize_chain_anchor.sh`, both at
      **Hour=7 Minute=10** daily (both plists born the same second,
      2026-09-11 17:48:47 — installed together, and never de-duplicated).
   2. **Double-notarize predates even those plists**: local pairs of entries
      with the same tip, ts microseconds apart, appear on **31 of 46 days,**
      from 2026-08-29 — an earlier double-run mechanism; the twins made it
      guaranteed.
   3. **The remote sibling is a BACKFILL artefact, not a direct double-push of
      one entry.** The two racing jobs both compute `seq = len(local)+1`, so a
      single seq can acquire two signed entries (measured: seq 101 exists
      under two names, `.451918` and `.806307`). The next day's backfill —
      which dedupes the remote **BY SEQ ONLY** (`set(range(1, seq)) -
      remote_seqs`) — re-pushes local entry 102 (ts `.806307`) into name
      `000102-…806307…`; two racing backfills did that twice within 0.8s
      (receive-times 07:10:40.843 and 07:10:41.649 on 2026-10-11), and Drive
      keeps same-name siblings. Every sibling's receive-date is a *later date*
      than its notarize-date — the backfill signature — which was invisible
      until this was measured directly.

   **The root-cause choke point is fixed on this branch: a single-instance
   lock in `notarize_chain_anchor.sh`** (mkdir-atomic, `MSB_NOTARY_LOCK`
   overridable; a held lock makes a second runner *skip* with a diagnostic
   naming both schedules, rather than double-push). Three regression tests in
   `tests/ops/test_notarize_lock.py` — free-acquire-and-release, second-runner
   skip without touching the held lock, stale lock doesn't wedge — pass; with
   the block excised the mutant compiles and 2 of 3 fail (mutation-checked).
   Two residual owner choices remain: which of the two notary jobs to retire,
   and whether to make the byte-level write path whole (`copyto` overwrite
   recognition) — neither is a code defect.
5. **Local `main` cannot be pushed as-is.** It is ahead of `origin/main` with
   **11 of its commits marked `[do-not-push]`** (BLOCKED gate evidence,
   2026-09-28 → 10-09), and this repo's `.git/hooks/pre-push` has **no**
   `[do-not-push]` guard — the guard that exists in the PZS repo
   (`scripts/lib/held_commits.sh`) is not present here. A naive `git push` would
   publish deliberately-held evidence. The fix was therefore pushed on a branch
   based on `origin/main`, not on `main`.
6. **The pre-push hook gates the working tree, not the commits**, and this
    working tree carries 48 unrelated modified files — so any push from this
    checkout is entangled with edits that have nothing to do with it.
7. **The record layer gates docs counts.** `scripts/doc_records.py` cross-checks
    a claimed "Markdown under `docs/`: N files / M lines" in
    `docs/what-msb-v3-is.md` against the live tree, so adding *any* record changes
    those numbers and blocks a push until the claim is updated. Updating the claim
    is part of adding a record — not a bypass, and not a reason to reach for
    `MSB_SKIP_PORTABILITY=1`.
8. **Local `main`'s copy of `docs/what-msb-v3-is.md` is dirty** with a different
    set of numbers (155 files / 37,218 lines, dated 2026-09-26) that already
    disagrees with the live tree. The live tree now measures **158 files /
    39,070 lines**, so *both* the committed claim (153 / 35,405) and the dirty
    edit (155 / 37,218) are stale — the dirty edit had already been overtaken.
    `doc_records` therefore fails on `main` independently of this change, and
    regenerating the claim is somebody's WIP decision, not mine.