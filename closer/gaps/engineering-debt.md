# Engineering Debt — Things That Reduce Reliability/Maintainability

## D1: Mypy Errors Pre-Existing in Phases 3-4
**Status**: Partially fixed (11 errors resolved in 2a54a22)
**Remaining**: 0 mypy errors in plei/ (verified)
**Note**: Some pre-existing warnings in non-plei code may still exist

## D2: Test Suite Takes >2 Minutes
**Status**: Full suite times out at 60s+ in CI
**Impact**: Slow feedback loop, CI may timeout
**Fix**: Parallelize tests, split into tiers (fast/unit vs slow/integration)

## D3: No Type Stubs for External Dependencies
**Status**: mypy runs with --ignore-missing-imports for some deps
**Impact**: Reduced type safety
**Fix**: Add py.typed markers or stubs for critical deps

## D4: conversation/envelope.py Has STUB_MODE
**Status**: `STUB_MODE` env var allows stub responses
**Impact**: Could mask real failures in development
**Fix**: Remove or gate behind explicit test flag

## D5: Multiple Duplicate Repair Systems
**Status**: ops/repair.py, ops/auto_repair.py, ops/root_cause.py, ops/verify.py all exist
**Impact**: Unclear which to use when
**Fix**: Consolidate or document the repair pipeline

## D6: Evidence Spine and Audit Chain Are Separate Systems
**Status**: evidence/spine.py and uac/audit_chain.py both track decisions
**Impact**: Two sources of truth for decision provenance
**Fix**: Cross-reference or consolidate

## D7: No Structured Logging Standard
**Status**: Mixed use of logger, print, sys.stderr
**Impact**: Inconsistent observability
**Fix**: Standardize on structured logging with correlation IDs

## D8: requirements.txt Not Pinned
**Status**: Dependencies may drift between environments
**Impact**: Reproducibility risk
**Fix**: Pin all dependencies with hashes
