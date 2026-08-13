# Changelog

All notable changes to msb-v3 are recorded here. Format follows [Keep a
Changelog](https://keepachangelog.com/en/1.1.0/); versioning is [SemVer](https://semver.org/).

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

<!-- Release links pending a configured remote; tags are local (v0.2.0,
     SMI-017-v1.0). -->
