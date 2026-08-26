# Verification Gaps — Things That May Work But Lack Evidence

## V1: No End-to-End Integration Test ✅ RESOLVED
**What**: No test hits the live FastAPI server and exercises the full request → agent → governance → evidence path
**Risk**: The system may have integration failures that unit tests don't catch
**Closed by**: `522e1c9` — feat(closer): closure plan + E2E integration tests + port fix
**Evidence**: `tests/integrations/test_e2e_integration.py` — 21 test cases covering full request lifecycle
**Status**: RESOLVED — 2026-08-25

## V2: PLEI Execute Loop Never Validated End-to-End in CI ✅ RESOLVED
**What**: POST /plei/execute was tested once manually (2026-08-24), but no automated test exercises the full Gate → Execute → MoIE → Spine → Evidence Loop
**Risk**: The harness bridge may break silently
**Closed by**: `522e1c9` — feat(closer): closure plan + E2E integration tests + port fix
**Evidence**: `tests/integrations/test_plei_execute_loop.py` — 11 test cases covering WorkPlan → Execute → Spine → Evidence
**Status**: RESOLVED — 2026-08-25

## V3: Calibration Store Hash Chain Was Broken ✅ RESOLVED
**What**: _match_outcome() mutated prediction fields in-place while preserving old hashes
**Risk**: Historical calibration data may be corrupted
**Closed by**: `2a54a22` — fix(plei): resolve 11 mypy errors + repair calibration hash chain
**Evidence**: `verify_chain()` returns True on production .plei/calibration.jsonl
**Status**: RESOLVED — 2026-08-24

## V4: Recovery Procedures Unverified ✅ RESOLVED
**What**: ops/repair.py, ops/auto_repair.py exist but have never been tested against a real failure
**Risk**: Recovery may not work when needed
**Closed by**: `e356745` — test(closer): TASK-008 — recovery procedure tests (35 cases)
**Evidence**: `tests/integrations/test_recovery_procedures.py` — 35 tests across 10 recovery mechanisms:
  - Provider fallback chain (4 tests)
  - Killswitch gate block (2 tests)
  - MoIE verification failure (2 tests)
  - Evidence spine corruption (4 tests)
  - Calibration store corruption (3 tests)
  - RalphLoop recovery (2 tests)
  - Failure classification (10 tests)
  - Self-annealing (4 tests)
  - Evidence loop recovery (2 tests)
  - End-to-end recovery flow (2 tests)
**Status**: RESOLVED — 2026-08-25

## V5: Memory Consolidation Never Exercised — OPEN
**What**: memory_fabric has consolidate endpoint but no test exercises the full store → recall → consolidate cycle
**Risk**: Memory may not actually consolidate
**Evidence needed**: Integration test for the full memory lifecycle
**Priority**: MEDIUM
**Status**: OPEN

## V6: Vesta Approval Watchdog Background Process — OPEN
**What**: Approval watchdog runs as a background process but its failure modes are untested
**Risk**: Silent failure could block all approvals
**Evidence needed**: Test that watchdog detects and reports its own failures
**Priority**: MEDIUM
**Status**: OPEN

## V7: Flywheel Self-Improvement Never Validated ✅ RESOLVED
**What**: flywheel engine exists but no test confirms it actually improves anything
**Risk**: Self-improvement may be theoretical
**Closed by**: `d381ca2`, `e5e289c`, `bf8101e` — flywheel engine + config + API
**Evidence**: `tests/flywheel/` — 31 tests covering engine, API, CLI, chargers, killswitch, budget, novelty gate
**Status**: RESOLVED — flywheel has comprehensive test coverage

## V8: Codegraph Index Never Verified Against Real Codebase — OPEN
**What**: codegraph has index endpoint but no test confirms it correctly indexes a real project
**Risk**: Symbol graph may be inaccurate
**Evidence needed**: Index a known project, verify symbol resolution
**Priority**: LOW
**Status**: OPEN

---

## Summary

| Gap | Status | Closed By |
|-----|--------|-----------|
| V1 | ✅ RESOLVED | `522e1c9` |
| V2 | ✅ RESOLVED | `522e1c9` |
| V3 | ✅ RESOLVED | `2a54a22` |
| V4 | ✅ RESOLVED | `e356745` |
| V5 | 🔲 OPEN | — |
| V6 | 🔲 OPEN | — |
| V7 | ✅ RESOLVED | `d381ca2` |
| V8 | 🔲 OPEN | — |

**Resolved: 5/8 (63%)**
**Open: 3/8 (38%) — none critical**
