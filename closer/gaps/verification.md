# Verification Gaps — Things That May Work But Lack Evidence

## V1: No End-to-End Integration Test
**What**: No test hits the live FastAPI server and exercises the full request → agent → governance → evidence path
**Risk**: The system may have integration failures that unit tests don't catch
**Evidence needed**: A test that starts the server, sends a request, verifies the response, and confirms evidence was recorded
**Priority**: HIGH — this is the single biggest verification gap

## V2: PLEI Execute Loop Never Validated End-to-End in CI
**What**: POST /plei/execute was tested once manually (2026-08-24), but no automated test exercises the full Gate → Execute → MoIE → Spine → Evidence Loop
**Risk**: The harness bridge may break silently
**Evidence needed**: Integration test that creates a WorkPlan, executes it, and verifies the evidence chain
**Priority**: HIGH

## V3: Calibration Store Hash Chain Was Broken
**What**: _match_outcome() mutated prediction fields in-place while preserving old hashes
**Risk**: Historical calibration data may be corrupted
**Current state**: Fixed in commit 2a54a22, chain rebuilt
**Evidence needed**: Verification that existing .plei/calibration.jsonl is clean
**Priority**: MEDIUM — already fixed, just needs confirmation

## V4: Recovery Procedures Unverified
**What**: ops/repair.py, ops/auto_repair.py exist but have never been tested against a real failure
**Risk**: Recovery may not work when needed
**Evidence needed**: Chaos test or simulated failure + recovery
**Priority**: MEDIUM

## V5: Memory Consolidation Never Exercised
**What**: memory_fabric has consolidate endpoint but no test exercises the full store → recall → consolidate cycle
**Risk**: Memory may not actually consolidate
**Evidence needed**: Integration test for the full memory lifecycle
**Priority**: MEDIUM

## V6: Vesta Approval Watchdog Background Process
**What**: Approval watchdog runs as a background process but its failure modes are untested
**Risk**: Silent failure could block all approvals
**Evidence needed**: Test that watchdog detects and reports its own failures
**Priority**: MEDIUM

## V7: Flywheel Self-Improvement Never Validated
**What**: flywheel engine exists but no test confirms it actually improves anything
**Risk**: Self-improvement may be theoretical
**Evidence needed**: Test that flywheel produces measurable improvement
**Priority**: LOW

## V8: Codegraph Index Never Verified Against Real Codebase
**What**: codegraph has index endpoint but no test confirms it correctly indexes a real project
**Risk**: Symbol graph may be inaccurate
**Evidence needed**: Index a known project, verify symbol resolution
**Priority**: LOW
