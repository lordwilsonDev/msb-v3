# Changelog

All notable changes to msb-v3 are recorded here. Format follows [Keep a
Changelog](https://keepachangelog.com/en/1.1.0/); versioning is [SemVer](https://semver.org/).

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

[0.2.1]: https://github.com/lordwilsonDev/msb-v3/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/lordwilsonDev/msb-v3/compare/SMI-017-v1.0...v0.2.0
[SMI-017-v1.0]: https://github.com/lordwilsonDev/msb-v3/releases/tag/SMI-017-v1.0
