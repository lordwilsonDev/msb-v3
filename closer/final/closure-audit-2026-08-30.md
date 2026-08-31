# MSB v3 — Closure Audit (2026-08-30)

**Generated:** 2026-08-30 ~22:45 UTC
**HEAD:** `1b80a74` (branch `main`, fully pushed, tree clean bar telemetry jsonl)
**Supersedes:** `closer/final/closure-report.md` (2026-08-26, "CLOSED — 100%") and
`closer/final/convergence-closure-ledger.md` (2026-08-28, HEAD `df5a49f8`). Both are
stale: HEAD has moved 40+ commits since the ledger, and the "100% CLOSED" report was
never re-verified against a moving HEAD.

**Verdict: CLOSING — not CLOSED.** Runtime and core engineering are solid and
independently verified. Closure is held open by 3 real items + 1 human-blocked item.

---

## Method

Verified against real runtime, real tests, real CI — not against the prior reports.
All checks run this session on HEAD `1b80a74`.

## Verified this session (evidence)

| Claim | Evidence | State |
|---|---|---|
| 3 primary CI gates green on HEAD | `gh run list` → `msb-v3 CI` / `factory-gate` / `harness-gate` all `success` on `1b80a74df` (2026-08-29 12:24) | ✅ VERIFIED |
| Runtime healthy | `/status` → `ready:true`, qwen3:8b, `127.0.0.1:8766`, launchd-supervised | ✅ VERIFIED |
| Governed chat path executes | `mcp__msb-v3__chat` → `ok:true`, exact output, real qwen3:8b round-trip | ✅ VERIFIED |
| Core canonical/contract/gateway/action-gate/lifecycle tests | 484 passed, 0 failed (local, 27s) | ✅ VERIFIED |
| ProviderContract v1 conformance suite | `tests/contracts/test_provider_contract.py` — 190 collected, all pass | ✅ VERIFIED (unit) |
| Gateway canonical-path tests | `tests/architecture/test_gateway_canonical.py` — 7 pass | ✅ VERIFIED (unit) |
| Codegraph unit tests | 65 pass | ✅ VERIFIED (unit) |
| ops-script regression suite | `scripts/test-ops.sh` — 39 passed, 0 failed | ✅ VERIFIED |
| `ruff check .` | All checks passed | ✅ VERIFIED |
| Full pytest collection | 3058 tests collected | — |
| Meta-System sequencing exception | dated block in `docs/blueprints/convergence-to-12/v4-parking-lot.md` (2026-08-28) | ✅ VERIFIED |
| C5 CLI sandbox risk waiver | written acceptance 2026-08-26, reversal trigger defined | ✅ WAIVED |

## Open — blocks closure

### O1 — `release-verify` RED (CRITICAL)
`release-verify` (virgin-clone full suite) **failed** on `bc08a93`: **3 failed, 3036 passed, 13 skipped** (502s).
The 3 are the known non-hermetic infra tests (llama-server / httpx-timeout / chaos-proxy) that
depend on the always-on launchd `:8766` server a fresh clone cannot provide.
- Behaviour is fine; the gate is red.
- This is Task 7 (`make portability` non-hermetic) in `docs/superpowers/plans/2026-08-27-ci-isolation-fix.md`.
- **A clean release tag cannot be cut until this is green.**

### O2 — Release tag stale + unverified (CRITICAL)
`v0.4.0` points at `bc08a93` — **5 commits behind HEAD**, and `release-verify` is RED on that commit.
The ledger's own Desktop Gate condition #4 ("release tag exists and is CI-verified") is **not met**.

### O3 — Gateway not bypass-impossible (CRITICAL — or revise the criterion)
`gateway/` is an **audit entry point** on `agent/handle.py` (`try/except`, best-effort); `ActionGate`
remains the enforcement layer. The interchangeability checklist criterion — *"a production-path
capability invocation must be impossible without passing through the Gateway/contract boundary"* —
is **not met**. Self-documented as weaker than the checklist. Either (a) make it bypass-impossible
with a fail-closed bypass test, or (b) formally accept the dual-governance model in writing and
amend the checklist with rationale.

### O4 — Codegraph capability EXISTS but is not EXECUTED (VERIFICATION)
V8 in `closure-report.md` claims codegraph index "RESOLVED". Unit tests pass (65), but
`codegraph_stats` for `~/msb-v3` returns **0 nodes / 0 edges** — the repo is not actually indexed.
The Meta-System architecture-graph component depends on `codegraph/`. Either populate + verify the
index (`codegraph_stats` non-zero, consistent with source) or downgrade V8 to "unit-tested, not
indexed" with the index population as a named deferred task.

## Blocked — human action (not closure-counted, not code)

### C1 — DeepSeek API (402, credits exhausted)
Primary provider seam unverifiable end-to-end until credits are refilled.
Verify: `curl -s https://api.deepseek.com/v1/models` → 200, then run the live provider seam test.
Fallback chain works (paseo.claude caught the 402 in the 2026-08-24 live test).

## Engineering debt — non-blocking

- **MemoryStore deprecation**: `DeprecationWarning` fires on every request / test setup
  (`core/container.py:167`). Migration to `memory_fabric.store` documented, not done.
- **`ruff format` drift**: 516 files would be reformatted. CI runs `ruff check` only, so not a gate failure.
- **Stale governance docs**: `docs/governance/convergence-state.md` dated 2026-08-28, describes an
  older HEAD. `closer/final/closure-report.md` claims "100% CLOSED" against a HEAD 40+ commits back.

## Closure score (verified work only)

Prior report: "100% — 22/22, all decisions made." **Not defensible against current HEAD.**

Closure-critical items: **5** (release truth, gateway canonical, ProviderContract v1, Meta exception, codegraph capability)
- Fully VERIFIED: 2 (ProviderContract v1, Meta exception)
- Open: 3 (O1 release-verify, O2 tag — one dependency chain; O3 gateway; O4 codegraph)
- Waived: C5 (CLI sandbox) · Blocked-human: C1 (DeepSeek billing)

**Closure ≈ 60–65%.** Status: **CLOSING**. Not `CLOSED` while O1–O4 are unverified.

## Dependency-ordered path to CLOSED

1. **O1** — hermeticise or `importorskip`-gate the 3 `release-verify` tests so the virgin-clone
   suite is green with no dependence on launchd `:8766`.
   *Acceptance:* `gh run` green on `release-verify` for a fresh commit. → blocks 2.
2. **O2** — cut `v0.4.1` / `v0.5.0` on current HEAD after O1.
   *Acceptance:* tag SHA == HEAD; all 4 workflows green on the tag SHA.
3. **O3** — gateway: make bypass-impossible + fail-closed bypass test, **or** write the
   dual-governance acceptance and amend the checklist.
   *Acceptance:* checklist item met or explicitly revised with rationale + test.
4. **O4** — codegraph: populate + verify the repo index, **or** downgrade V8 with the
   index-population task named and deferred.
   *Acceptance:* `codegraph_stats` non-zero and source-consistent, **or** ledger downgrade recorded.
5. **DOC** — refresh `closer/final/` + `convergence-state.md` to HEAD; retire/supersede the
   "100% CLOSED" report.
6. **C1** (Wilson) — DeepSeek billing → live provider seam test.
7. **DEBT** (non-blocking) — MemoryStore→memory_fabric migration; `ruff format` the 516-file drift
   or add `ruff format --check` to CI.

## Anti-scope-creep

- The **EAAE blueprint** does **not** enter this closure plan — "future project" until O1–O3 are VERIFIED.
- **Meta-System** and **Desktop Phase B** stay parked behind their own gates (unchanged).
- No "while we're in here" work joins this plan. Closure = O1–O4 verified + docs refreshed.

---

## Update — 2026-08-31: O1 closed, O2 substantially closed (v0.4.2)

Executed PRODUCTION-CLOSURE-001 P0–P2.

- **O1 — CLOSED, CI-proven.** `release-verify` self-provisions a run-scoped
  server (`scripts/ci-runtime.sh`), gates on the tiered core (`-m "not live"`),
  and is **green from a virgin clone on `v0.4.2`** (3040 passed) — the first
  green `release-verify` since `v0.3.0`.
- **Root cause of every prior failure** (v0.3.2 / v0.4.0 / v0.4.1): NOT the
  "llama-server / httpx / chaos-proxy" theory. It was
  `tests/plei/test_plei_is_msb_v3.py` — `ingest_all()` took the project name
  from the checkout dir basename; a `/tmp/msb-verify-*` clone read as
  `msb-verify-*`. Fixed: reads `pyproject.toml [project].name`.
- **O2 — substantially closed.** Version identity reconciled to `0.4.2` across
  all 4 sources + `desktop/package.json`. A CI-verified tag exists on a commit
  green on 3 primary gates AND `release-verify` — the O2 invariant holds.
  Residue: no *machine*-enforced "tag only on green" (ruleset limitation).
- **v0.4.1** is a dead immutable tag (its `release-verify` failed on the plei
  bug). `v0.4.2` is the fixed re-cut. See `docs/releases/v0.4.2.md`.
- **P3 scoped** — `docs/releases/O3-AUTHORITY-CLOSURE-PLAN.md`. Not started:
  own multi-session block, needs the Option A/B decision first.

### Revised closure picture

| Item | Was (8/30) | Now (8/31) |
|---|---|---|
| O1 release-verify | RED | ✅ CLOSED (green on v0.4.2) |
| O2 tag / version truth | stale + unverified | ✅ substantially closed (v0.4.2 CI-verified) |
| O3 gateway bypass-impossible | open | scoped, not started (own block) |
| O4 codegraph index | open | open (unchanged) |
| C1 DeepSeek billing | blocked-human | blocked-human (unchanged) |

Closure ≈ 80% of the O-series. Remaining: O3 (large), O4 (small), + Phases
17/18 debt.

---

## Update — 2026-08-31 (cont.): P3 CLOSED, O4 resolved

- **O3 / P3 — CLOSED** (`e60d783`). Decision: Option B (dual-governance,
  `docs/governance/authority-model.md`). All 14 entry paths mapped to first
  capability execution — zero UNKNOWN (7 ALLOW-through-authority, 4
  CONSTRAINED with documented narrower authority, 3 READ-ONLY). Proof:
  `tests/architecture/test_authority_boundary.py` (16 cases incl. an
  adversarially-verified bypass scanner). No production code changed — the
  boundary already held.
- **O4 — resolved (was a false alarm).** `codegraph_stats` returned "0 nodes"
  in the 8/30 audit because it was queried with the **absolute path** as the
  repo key; the index is keyed `"msb-v3"`. Under the right key it returns a
  full graph. The stored index was ~2 weeks stale (Aug 15) — **re-indexed
  2026-08-31**: 1843→**7114 nodes**, 12020→**42232 edges**
  (file/module/class/function/method; calls/contains/imports/inherits/
  references). Canonical repo key: `"msb-v3"` (not a path). The capability is
  EXECUTED + VERIFIED; the "V8 resolved" claim stands.

### O-series: closed

| Item | Status |
|---|---|
| O1 release-verify | ✅ CLOSED (green virgin-clone on v0.4.2) |
| O2 release truth | ✅ substantially closed (v0.4.2 CI-verified) |
| O3 authority boundary | ✅ CLOSED (Option B, 14-path proof, e60d783) |
| O4 codegraph | ✅ resolved (key mismatch; re-indexed 7114 nodes) |
| C1 DeepSeek billing | blocked-human (unchanged) |

**All four O-items closed.** Remaining production-readiness work is P4
(provider failure matrix + interchange), P5 (routing measurable), and
Wave 2 H1–H15 — see `docs/releases/PRODUCTION-READINESS-ROADMAP.md`.
