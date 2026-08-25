# MSB v3 Closure Plan

## Closure Criteria

A project is CLOSED when:
1. All critical gaps are resolved
2. All verification gaps have evidence
3. Test suite passes in CI
4. No critical blockers remain
5. Documentation is sufficient for another engineer to operate

## Phase 1: Runtime Closure (CRITICAL)

### TASK-001: Refill DeepSeek API Credits
- **Status**: PLANNED
- **Priority**: CRITICAL
- **Dependencies**: None
- **Acceptance**: api.deepseek.com returns 200
- **Verification**: curl test
- **Risk**: LOW — just account management

### TASK-002: Resolve Port Conflict (:8080)
- **Status**: PLANNED
- **Priority**: CRITICAL
- **Dependencies**: None
- **Acceptance**: llama-server starts on non-conflicting port
- **Verification**: health check
- **Risk**: LOW

### TASK-003: Free Disk Space to <85%
- **Status**: PLANNED
- **Priority**: CRITICAL
- **Dependencies**: None
- **Acceptance**: df -h shows <85%
- **Verification**: disk check
- **Risk**: MEDIUM — may require archiving evidence

### TASK-004: Trigger CI and Fix Failures
- **Status**: PLANNED
- **Priority**: CRITICAL
- **Dependencies**: TASK-001, TASK-002
- **Acceptance**: GitHub Actions green on main
- **Verification**: CI badge
- **Risk**: MEDIUM

## Phase 2: Verification Closure (HIGH)

### TASK-005: End-to-End Integration Test
- **Status**: PLANNED
- **Priority**: HIGH
- **Dependencies**: TASK-004
- **Acceptance**: Test starts server, sends request, verifies response + evidence
- **Verification**: pytest passes
- **Risk**: MEDIUM

### TASK-006: PLEI Execute Loop Integration Test
- **Status**: PLANNED
- **Priority**: HIGH
- **Dependencies**: TASK-004
- **Acceptance**: Test creates WorkPlan, executes through harness, verifies spine
- **Verification**: pytest passes
- **Risk**: LOW

### TASK-007: Calibration Chain Verification
- **Status**: PLANNED
- **Priority**: MEDIUM
- **Dependencies**: None
- **Acceptance**: verify_chain() returns True on production data
- **Verification**: Script output
- **Risk**: LOW

### TASK-008: Recovery Procedure Test
- **Status**: PLANNED
- **Priority**: MEDIUM
- **Dependencies**: TASK-004
- **Acceptance**: Simulated failure + successful recovery
- **Verification**: Test output
- **Risk**: MEDIUM

## Phase 3: Governance Closure (HIGH)

### TASK-009: ActionGate End-to-End Test
- **Status**: PLANNED
- **Priority**: HIGH
- **Dependencies**: TASK-004
- **Acceptance**: Gate blocks unsafe action, permits safe action
- **Verification**: Contract test
- **Risk**: LOW

### TASK-0010: Killswitch Verification
- **Status**: PLANNED
- **Priority**: HIGH
- **Dependencies**: TASK-004
- **Acceptance**: Killswitch stops all execution
- **Verification**: Test output
- **Risk**: LOW

### TASK-0011: Budget Enforcement Test
- **Status**: PLANNED
- **Priority**: MEDIUM
- **Dependencies**: TASK-004
- **Acceptance**: Budget limits are enforced
- **Verification**: Test output
- **Risk**: LOW

## Phase 4: Documentation Closure (MEDIUM)

### TASK-0012: README Update
- **Status**: PLANNED
- **Priority**: MEDIUM
- **Dependencies**: TASK-004
- **Acceptance**: README reflects actual current state
- **Verification**: Manual review
- **Risk**: LOW

### TASK-0013: API Documentation
- **Status**: PLANNED
- **Priority**: MEDIUM
- **Dependencies**: TASK-004
- **Acceptance**: All endpoints documented
- **Verification**: OpenAPI spec
- **Risk**: LOW

### TASK-0014: Architecture Decision Records
- **Status**: PLANNED
- **Priority**: LOW
- **Dependencies**: None
- **Acceptance**: Key decisions documented with rationale
- **Verification**: docs/ directory
- **Risk**: LOW

## Phase 5: Security Closure (MEDIUM)

### TASK-0015: Secret Scan
- **Status**: PLANNED
- **Priority**: MEDIUM
- **Dependencies**: None
- **Acceptance**: No secrets in code
- **Verification**: trufflehog or similar
- **Risk**: LOW

### TASK-0016: Permission Scope Test
- **Status**: PLANNED
- **Priority**: MEDIUM
- **Dependencies**: TASK-004
- **Acceptance**: Agents cannot exceed permissions
- **Verification**: Security test
- **Risk**: LOW

## Dependency Graph

```
TASK-001 (DeepSeek) ──┐
TASK-002 (Port) ──────┼──→ TASK-004 (CI) ──→ TASK-005 (E2E Test)
TASK-003 (Disk) ──────┘         │
                                ├──→ TASK-006 (PLEI Execute Test)
                                ├──→ TASK-008 (Recovery Test)
                                ├──→ TASK-009 (ActionGate Test)
                                ├──→ TASK-0010 (Killswitch Test)
                                ├──→ TASK-0011 (Budget Test)
                                ├──→ TASK-0012 (README)
                                └──→ TASK-0013 (API Docs)

TASK-007 (Calibration) ─── independent
TASK-014 (ADRs) ────────── independent
TASK-015 (Secret Scan) ─── independent
TASK-016 (Permission Test) → depends on TASK-004
```

## Closure Score

```
Total Critical Tasks:    4
Verified Critical:       0
Closure:                 0%

Total High Tasks:        4
Verified High:           0

Total Medium Tasks:      5
Verified Medium:         0

Total Low Tasks:         1
Verified Low:            0
```

**PROJECT STATUS: OPEN**
**FINAL VERDICT: Not closable until critical gaps resolved and CI passes**
