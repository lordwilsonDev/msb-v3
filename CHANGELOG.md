# Changelog

All notable changes to msb-v3 are recorded here. Format follows [Keep a
Changelog](https://keepachangelog.com/en/1.1.0/); versioning is [SemVer](https://semver.org/).

## [Unreleased]

### Added
- **Capability Gateway** (`src/msb_v3/gateway/`): the single
  dispatcher between runtime and (local|frontier) compute, mapped
  to the *Capability Gateway* plane in
  `docs/blueprints/plans/m1-governance-node-architecture.md` §3
  (Compute Plane) and the §5 (Experimental Plane) "no autonomous
  biological intervention" rule. Every call goes through `route()`
  which: (1) denies if any required capability token is missing,
  (2) denies if `requires_authorization=True` and the slug-keyed
  grant isn't held in the context (codified — not a moral principle),
  (3) routes to the active local backend (Ollama/llama.cpp via
  `local_ai.client_factory`) if `estimated_bytes` fits the
  configurable local budget (default 6 GB on M1), else to the
  frontier seam (`OPENAI_FRONTIER_URL`). Denials are recorded into
  `uac/audit_chain.py` alongside grants, so the runtime can answer
  "why was call X refused at 14:32 on Tuesday" after the fact. Tests:
  10 cases pinning allow/deny/local-vs-frontier/auth-required +
  audit-chain integration.
- **Capability Gateway wired into `ChatHarness.execute`**
  (the highest-volume LLM call site — every `/chat`,
  `/v1/chat/completions`, `conversation/ask`, and agent-tool "chat"
  call routes through it). Default path is unchanged (local backend,
  same routing as before) but every dispatch now records
  `decision_id` (audit-chain record hash) + `gateway_reason` into
  telemetry so the dispatch is replayable. Opt-in gate fields
  (`requires_authorization`, `required_capabilities`) turn on the
  full authorization + capability check; a denial returns
  `ok=False, event="chat:denied"` and NEVER contacts the model client
  nor falls back to `[fallback]` — a denied call is an event, not a
  degraded outcome. Tests: 4 cases in
  `tests/test_gateway_harness_wiring.py` (default path reaches model
  + records decision, opt-in denial is loud and model-agnostic,
  denial lands in audit chain as `call.denied`, matching grant
  allows the call through).
- **`MSB_MULTIMODAL_ENABLED` feature flag for `/multimodal/*`**:
  VisionClaw / HapticHeartbeat / SpeechFunctions routes now return 503 +
  detail message when `MSB_MULTIMODAL_ENABLED` is unset (default off,
  fail-closed like `OPENAI_API_KEY`). A real implementation that stops
  returning `status="stub"` implies the gate should be opened; tests in
  `tests/triumvirate/test_multimodal_feature_flag.py` pin both sides
  (default-disabled, enabled-returns-payload, truthy-falsy edge cases).
  The original "stub calls must not inflate multimodal metrics" guard
  at the call site is preserved. ([`28d32a3`](https://github.com/lordwilsonDev/msb-v3/commit/28d32a3))
- **Subsystem decoder** (`docs/glossary.md`): the mythology
  (Triumvirate / Argus / Hippocampus / Hermes / Vesta / Ralph /
  Cockpit / VisionClaw / HapticHeartbeat / SpeechFunctions / Sacred
  Lock / Mulch) is a real architectural contract, not ceremony — but
  newcomers can't read any file without decoding the names. The
  glossary maps each name to its module path, intent, and "is it
  safe to rename?" verdict, and is cross-linked from `CLAUDE.md`
  under "Defaults". ([`9752932`](https://github.com/lordwilsonDev/msb-v3/commit/9752932))
- **Release verification from a virgin checkout** (`scripts/verify-release.sh`,
  `make verify-release`): proves a tag the way others will fetch it — resolves
  the tag (argv → `VERIFY_TAG` → `v<pyproject>`), fails fast if it was never
  pushed (`git ls-remote`), fresh-clones it, confirms the clone is virgin (no
  tracked `.env` / `runtime/` — a leak guard), runs the release-checklist
  version test, seeds the research-runtime fixtures, then runs the FULL suite
  with `MSB_HOME`/`MSB_REPO` pinned exactly like the portability gate — fails
  on any test failure or seeded-artifact skip, so the seeding workstream can't
  silently regress. Optional `EXPECTED_PASS` strict count; `VERIFY_KEEP` /
  `VERIFY_CLONE_DIR` for debugging. Validated against v0.2.3: 814 passed,
  3 skipped.
- **Auto-verify every release tag in CI**
  (`.github/workflows/release-verify.yml`): each `vX.Y.Z` tag push (and manual
  `workflow_dispatch`) runs the verifier on the self-hosted macOS runner with
  a token-authenticated remote (private-repo safe), failing the tag if the
  suite doesn't pass. Wiring guard test pins trigger → runner →
  server-ensure → verifier → token remote, so auto-verification can't
  silently drop off.
- **Release-tag immutability ruleset** (`release-tag-immutability`):
  `deletion` + `update` rules on `refs/tags/v*` — once a release tag is cut
  it can be neither deleted nor force-moved (live-verified: creation allowed,
  force-update and deletion rejected). Documented empirical finding in
  CLAUDE.md: GitHub cannot gate a tag's first creation on a required status
  check (checks exist on a tag ref only after a workflow ran on the tag —
  chicken-and-egg), so verification gates at the branch/CI level and the
  ruleset locks the verified state. Emergency path: disable → delete →
  re-enable.

### Fixed
- **Flaky chaos tests under load** (`src/sovereign_runtime/tests/test_chaos_phase2.py`):
  the concurrent-append test dropped records (300/400) when sqlite's default
  5s busy timeout expired under a saturated shared runner — `BEGIN IMMEDIATE`
  raised "database is locked" in a worker thread, and thread exceptions are
  swallowed by `t.join()`. Appends now retry lock contention with bounded
  backoff, worker failures are collected for the main thread to assert, and
  `AuditChain`'s busy timeout is 10s. The perf probe asserted a 10s wall-clock
  budget (observed 34s under load) — now a warm-up plus two load-cancelling
  halves with a 120s regression floor and a chain-rescan shape check.
  Stress-verified: 30×/10× runs under 4 CPU burners + fsync hammer all pass;
  factory-gate green on the first run with all gates concurrent.

### Changed
- **`src/sovereign_runtime/` package folded into `src/msb_v3/core/`**:
  `core/{health,identity}.py`, `events/event_bus.py`, `config/__init__.py`
  → `runtime_config.py`, `config/runtime.yaml` all promoted to
  `msb_v3/core/`. The full `brain/` subtree (RecursivePlanner, MoIE
  Swarm, AIL pipeline, plan models, planner memory) was deleted — the
  package-level disposition plan
  (`docs/blueprints/plans/2026-08-13-dormant-satellites-disposition.md`)
  had already documented these as stub/"non-implementation" months
  ago; deleting the package lets `pyproject.toml` testpaths shed
  `src/sovereign_runtime/tests` and lets `master` pytest invocation
  stop walking the dead-code path. The pre-existing
  `src/personal_intelligence/` is similarly dormant and awaits the
  same disposition pass. Test files moved: `test_runtime_boot.py` to
  `tests/`; `test_chaos_phase{1,2}.py` to `tests/chaos/`. CI workflow
  scope comments (`ci.yml`, `factory-gate.yml`) and docker-compose
  healthcheck probes (`import sovereign_runtime` →
  `from msb_v3.core.event_bus import EventBus`) updated. ([`08c2a3e`](https://github.com/lordwilsonDev/msb-v3/commit/08c2a3e))
- **Ceremony trim — 3 of 10 named subsystems**: `Cockpit`
  (`api/cockpit.py` → `api/dashboard.py`), `Argus`
  (`triumvirate/argus_auditor.py` → `observability/audit.py`,
  auditing *is* observability), `Ralph` (`agent/ralph_loop.py` →
  `agent/execution_loop.py`). Class/factory names (`ArgusAuditor`,
  `RalphLoopHarness`, `create_ralph_loop`) and API paths
  (`/assistant/ralph-loop`, `/cockpit/api`) kept verbatim — they
  appear in metric labels (`TRIUMVIRATE_*`), ledger event names
  (`event=ralph_loop:{completed,exhausted,…}`), and frozen docs
  (`docs/task-contract-v1.md`); renaming them is a wire-contract
  change that needs a separate, intentional pass with back-compat
  aliases. ([`08c2a3e`](https://github.com/lordwilsonDev/msb-v3/commit/08c2a3e))
- **Bare `except Exception:` block log discipline extended** to
  `api/home.py` (3 sites), `api/health.py` (2 sites), `api/chat.py` (1
  site), `db/sqlite.py` (1 site, before re-raise), and
  `harnesses/base.py` (1 site). Each was the "graceful degrade" pattern
  — return empty on failure — but the original exception was lost.
  `logger.debug("...", exc_info=True)` now fires before the early
  return so the specific failure mode is visible at debug level while
  the higher-level semantics (panel X unreadable, chat:fallback
  metric, transaction rolled back) are preserved.
  ([`00e1bbf`](https://github.com/lordwilsonDev/msb-v3/commit/00e1bbf))

## [0.2.3] - 2026-08-13

### Added
- **Research-runtime seeding** (`scripts/seed-research-runtime.sh`): the
  harness suite now runs from a fresh checkout everywhere. Committed
  per-slug fixtures under `tests/fixtures/research_runtime/` (evidence
  ledgers + ralph-loop `STATUS.json`) are seeded before the server boots in
  CI, the factory gate, and the portability staging copy —
  `claims_review` and the new ralph run-listing harness test no longer skip
  on unseeded environments (this was the 801-vs-802 portability delta).
  Adding a future seeded slug is a data-only change (drop a fixture dir).
- **Seeding wiring guard** (`tests/test_evidence_ledger_seed_wiring.py`, 11
  tests): pins the seeder into ci.yml / factory-gate.yml / portability
  ordering, the multi-slug loop, per-file no-clobber (real machine state is
  never overwritten), and the loud no-op guard.

### Fixed
- **Portability suite-leg redirect** (`portability-check.sh`): the harness
  gate exports `MSB_REPO` (the runner checkout), which silently redirected
  `scripts/test.sh`'s repo derivation — the full suite ran from the
  checkout instead of the staged foreign copy, defeating the portability
  guarantee. The suite invocation now pins `MSB_REPO="$DEST"`.
- **Skip reasons in suite output** (`scripts/test.sh`): pytest now defaults
  to `-rs`, so intentional skips (MSB_LIVE acceptance, seeded-run-ledger)
  are auditable in every local and CI run instead of hiding in a count.

## [0.2.2] - 2026-08-13

### Added
- **Release-checklist guard test** (`tests/test_release_versions.py`):
  asserts `pyproject.toml` / `msb_v3.__version__` / `sovereign_runtime`
  identity version agree — version drift now fails the suite at the source.
  (The v0.2.1 cut was blocked when `identity.py` lagged at 0.2.0; this test
  makes that impossible to ship.)

## [0.2.1] - 2026-08-13

### Added
- **Self-hosted harness-gate runner** (`msb-v3-mac-arm64`, labels
  `macOS, self-hosted`) supervised by a user-domain LaunchAgent — the
  browser + video-harness evidence gate now runs on the sovereign box
  instead of queueing forever on hosted runners. Fresh-machine
  registration runbook: vault 30-012.
- **Daily evidence freshener** (`com.blackswanlabz.harness-evidence`, 06:30):
  re-runs the three video-harness baseline experiments when evidence ages
  past 12h, so harness-gate's 24h freshness window never blocks on stale
  evidence; notification banner on failure.
- **CI pre-flight self-heal**: harness-gate runs the freshener before the
  evidence gate, so a stale-evidence push refreshes itself instead of red.
- **CI wiring guard test** (`tests/test_harness_gate_wiring.py`) — asserts
  the pre-flight step exists, runs before the gate, and calls the freshener
  with `MSB_REPO` bound to the checkout (`PyYAML` added to the dev extra).
- **Codecov coverage upload**: `CODECOV_TOKEN` repo secret wired into
  `codecov-action@v4` with `fail_ci_if_error: true` — coverage (~80%) now
  lands on Codecov, and a revoked token turns the test job red instead of
  silently dropping.

### Fixed
- **Coverage upload never landed**: `codecov-action@v4` dropped tokenless
  uploads for main-repo pushes, so every upload failed with
  "Token required" while `fail_ci_if_error: false` hid it. Now
  token-authenticated and loud.

### Changed
- Docs: CLAUDE.md covers the runner, evidence freshener, and CODECOV_TOKEN
  rotation; vault 30-012 runbook.

## [0.2.0] - 2026-08-12

### Added
- **Release discipline**: first semver release after the solo-dev audit —
  `__version__` is now the single source of truth for `/system/info`,
  `/system/config`, and `/mcp/status` (previously hardcoded strings that could
  drift). This CHANGELOG + git tag `v0.2.0` are the declaration of done.
- **CI scope fix**: mypy now gates **all three packages** (`msb_v3`,
  `sovereign_runtime`, `personal_intelligence`) — previously only `src/msb_v3`
  was typed, leaving the other two (and 6 latent type errors) in a blind spot.
  Now proven clean across all 128 source files, and blocking.
- **Blocking pip-audit** in CI: dependency scan was previously
  `continue-on-error` (silently red). Now runs `pip-audit` against
  `pip freeze --exclude-editable` (the version-proof way to skip private
  editable packages) and **fails the gate** on findings.
- **Coverage floor raised** 65 → 70 in the factory gate.
- **Fail-closed governance fixes committed**: `approval.py` / `guard.py`
  (previously uncommitted working-tree changes) are now in the release.

### Changed
- **Silent except sweep (~37 sites)**: bare `except: pass` swallows across the
  codebase (agent loop, planner, intent, research, rag, flywheel, harnesses,
  API routers, mcp_bridge) now log a warning instead of vanishing. The 5
  remaining `except: pass` sites are **intentional negative self-tests** in
  `producer.py`/`task_producer.py` (the `pass` IS the assertion) plus the
  documented metrics idempotency guard — each annotated with a comment.
- **Stub metrics honesty**: `/triumvirate/multimodal/*` endpoints no longer
  count `VisionClaw` / `HapticHeartbeat` / multimodal stub calls as real work —
  only non-stub payloads increment the counters, so the metric reflects reality
  until real implementations land.
- **pytest 8.3.5 → 9.0.3** (dev dependency): 8.3.5 had PYSEC-2026-1845.

### Fixed
- 5 mypy errors: `health.py` (callable-as-type ×3 — annotation-only fix),
  `test_personal_intelligence.py` (unchecked `ContextChunk | None` deref),
  `chargers.py` (list comprehension typing).
- `mcp_bridge.py` logger defined before `import logging` (import-order break
  from the sweep reorder).

### Removed
- 3 stray `.env.bak.*` files from the repo root (untracked secrets backups).

## [SMI-017-v1.0] - 2026-07 (pre-semver)

### Added
- SMI-017 release artifacts milestone (pre-semver). See tag `SMI-017-v1.0`.

[0.2.3]: https://github.com/lordwilsonDev/msb-v3/compare/v0.2.2...v0.2.3
[0.2.2]: https://github.com/lordwilsonDev/msb-v3/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/lordwilsonDev/msb-v3/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/lordwilsonDev/msb-v3/compare/SMI-017-v1.0...v0.2.0
[SMI-017-v1.0]: https://github.com/lordwilsonDev/msb-v3/releases/tag/SMI-017-v1.0
