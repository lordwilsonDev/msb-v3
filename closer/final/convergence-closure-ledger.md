# MSB v3 — Convergence Closure Ledger

**Generated:** 2026-08-28
**HEAD:** df5a49f8e6e4794a7a21df264cb5605363fb526e
**Version:** v0.3.2-41-gdf5a49f
**Branch:** main
**Status:** RED — lint fix applied locally, CI not yet re-verified

---

## BLOCKER STATUS

### C1: Release Truth / CI — IMPLEMENTED, AWAITING CI RE-VERIFICATION

| Field | Value |
|---|---|
| **Blocker** | CI is RED on HEAD |
| **Current State** | factory-gate + msb-v3 CI fail on lint (I001 in test_benchmark.py). harness-gate fails on infrastructure (self-hosted runner lost communication). |
| **Evidence** | gh run list shows 3 failures on commit df5a49f. ruff check locally produces 1 error (I001 — unsorted import block). mypy: 0 errors. claims: PASS (16 claims, 27 evidence paths). policy: PASS (baseline MATCH). |
| **Required Change** | Fix I001 lint error in tests/benchmark/test_benchmark.py (extra blank line removed). Commit and push. Re-run CI. |
| **Verification** | ruff check . — All checks passed. mypy src — Success: no issues. verify-claims.py — PASS. ci-policy-gate.sh — MATCH. |
| **Status** | IMPLEMENTED — lint fix applied locally, needs commit + push + CI re-verification |
| **Commit** | (pending) |

### C2: Gateway Canonical Path — IMPLEMENTED, LOCALY VERIFIED

| Field | Value |
|---|---|
| **Blocker** | Gateway exists but was not on the canonical execution path |
| **Current State** | `gateway/route.py` (222 lines) now called from both `agent/handle.py` (new) and `harnesses/base.py` (existing). The agent path calls `gateway_route()` at the top of `handle()` to record the compute decision into the audit chain. ActionGate remains the enforcement layer for tool-level gating. Gateway is the audit entry point; ActionGate is the enforcement layer. |
| **Evidence** | agent/handle.py line 31: `from msb_v3.gateway import GatewayCall, GatewayContext, route as gateway_route`. handle() calls `gateway_route(GatewayCall(name="agent.handle", ...), GatewayContext())` before any model calls. SURFACE.md updated: gateway reclassified from OPTIONAL to LOAD-BEARING. |
| **Required Change** | DONE. Agent path now routes through gateway for audit. Bypass test created. |
| **Verification** | tests/architecture/test_gateway_canonical.py — 7 tests, all pass. Tests verify: (1) handle.py imports gateway, (2) handle.py calls route(), (3) ChatHarness imports gateway, (4) ChatHarness calls route(), (5) governed paths have governance, (6) tool execution is gated. |
| **Status** | IMPLEMENTED — needs commit + push + CI verification |
| **Note** | The gateway is the audit entry point; ActionGate is the enforcement layer. This is a dual-governance model where gateway records the compute decision and ActionGate enforces tool-level authorization. The bypass test catches future regressions. |

### C3: ProviderContract v1 — IMPLEMENTED, LOCALY VERIFIED

| Field | Value |
|---|---|
| **Blocker** | No versioned provider contract exists |
| **Current State** | ProviderContract v1 defined in `agent/contract.py`. ProviderSpec extended with `contract_version` field. AgentProvider ABC extended with `health()` method. Conformance suite created in `tests/contracts/test_provider_contract.py` — 190 tests, all pass. |
| **Evidence** | src/msb_v3/agent/contract.py: ProviderContract dataclass + validate_contract() + contract_from_spec(). tests/contracts/test_provider_contract.py: 190 tests covering identity, capabilities, health, timeout, error semantics, execution, governance, evidence, contract version, registry conformance. |
| **Required Change** | DONE. Contract defined, providers migrated, conformance suite created. |
| **Verification** | tests/contracts/test_provider_contract.py — 190 tests, all pass. Each production provider (10 total: local.slice, api.deepseek, api.anthropic, dsh.headless, cli.claude, cli.codex, cli.opencode, paseo.claude, paseo.codex, paseo.opencode) is tested against all contract invariants. |
| **Status** | IMPLEMENTED — needs commit + push + CI verification |
| **Note** | ProviderContract v1 is a thin layer on top of ProviderSpec + AgentProvider. It adds: (1) contract_version field for forward compatibility, (2) health() method for runtime health checks, (3) validate_contract() for mechanical verification, (4) 190-test conformance suite that runs against every production provider. |

### C4: Meta-System Sequencing — AUTHORIZED, WRITTEN EXCEPTION IN v4-parking-lot.md

| Field | Value |
|---|---|
| **Blocker** | Meta-System (META-0 through META-8) exists in production surface but spine prerequisite was "not complete" per previous assessment |
| **Current State** | Meta-System formally authorized via written exception in v4-parking-lot.md (dated 2026-08-28). The spine prerequisite was stale — evidence/spine.py is FROZEN, used by 10+ modules, 82 test references, 9 dedicated tests. Meta's dependency IS satisfied. Classification: OPTIONAL (experimental tier) — honest: exists and tested, not yet battle-tested on real workloads. |
| **Evidence** | Written exception in docs/blueprints/convergence-to-12/v4-parking-lot.md with: why Meta exists now, prerequisite chain, evidence spine is complete, verification that closes the exception. SURFACE.md: msb_v3/meta = OPTIONAL (unchanged — honest classification). |
| **Required Change** | DONE. Formal exception written with evidence. Spine prerequisite verified as complete. Meta classified honestly as OPTIONAL (experimental tier). |
| **Verification** | Written exception includes: spine FROZEN status, 10+ module usage, 82 test references, 9 dedicated spine tests, ProviderContract v1 exists (190 tests), gateway canonical path exists (bypass test enforced). |
| **Status** | AUTHORIZED — needs commit + push + CI verification |
| **Note** | PATH B chosen: formal authorization with written exception. Meta stays in production surface at OPTIONAL classification. Promotion to LOAD-BEARING requires evidence that adaptive routing improves outcomes on real client data. |

---

## LOCAL GATE STATUS

| Gate | Status | Evidence |
|---|---|---|
| ruff check | ✅ PASS | All checks passed |
| mypy src | ✅ PASS | Success: no issues found in 346 source files |
| verify-claims.py | ✅ PASS | 16 claims, 27 evidence paths verified, 3 test-count claims match |
| ci-policy-gate.sh | ✅ PASS | baseline MSB-GATE-EVAL-001: MATCH |
| server health | ✅ PASS | {"ok":true,"service":"msb-v3","version":"0.3.1"} |

## CI STATUS (remote)

| Workflow | Status | Failure |
|---|---|---|
| factory-gate | ❌ FAIL | lint (I001) — now fixed locally |
| msb-v3 CI | ❌ FAIL | lint (I001) — now fixed locally |
| harness-gate | ❌ FAIL | infrastructure (self-hosted runner lost communication) |

## WORKING TREE DIRTY STATE

| File | Status | Notes |
|---|---|---|
| tests/benchmark/test_benchmark.py | modified | I001 fix (extra blank line removed) |
| .plei/calibration.jsonl | modified | pre-existing, unrelated |
| artifacts/hygiene/daily_gate_events.jsonl | modified | pre-existing, unrelated |

## KNOWN LIMITATIONS

| Item | Status | Action Required |
|---|---|---|
| C1: DeepSeek API | 🔒 BLOCKED | Refill credits (human action). External provider verification blocked. |
| C5: CLI provider sandboxing | ✅ RISK ACCEPTED | Written acceptance. Condition: if CLI execution becomes exposed to untrusted input, isolation review becomes mandatory. |
| MemoryStore deprecation | ⚠️ WARNING | DeprecationWarning on every request. Migration to memory_fabric.store documented but not completed. |
| 516 files need ruff format | ⚠️ TECH DEBT | ruff format --check shows 516 files would be reformatted. Not a CI failure (CI only runs ruff check, not ruff format). |

## EXPERIMENTAL SURFACES

| Surface | Classification | Evidence |
|---|---|---|
| speech | EXPERIMENTAL | SURFACE.md: OPTIONAL. No promotion criteria met. |
| energy_matrix | EXPERIMENTAL | SURFACE.md: OPTIONAL. No promotion criteria met. |
| meta | EXPERIMENTAL (proposed) | SURFACE.md: OPTIONAL. Spine prerequisite appears satisfied but governance not formally updated. |

## DESKTOP GATE

**BLOCKED** — convergence state is RED. Phase B (Electron desktop productization) cannot begin until:
1. CI is GREEN on exact HEAD
2. All four architectural blockers (C1-C4) are CLOSED
3. Fresh-observer audit passes
4. Release tag exists and is CI-verified

## RECOMMENDED NEXT STEPS

1. **Commit and push the lint fix** — unblocks C1 (CI green)
2. **Re-run CI** — verify all three workflows pass
3. **For C2 (Gateway):** Make a governance decision — is gateway the canonical path or a standalone routing layer? The current architecture has TWO orthogonal governance layers (gateway for compute routing, ActionGate for agent execution). Making gateway canonical would mean rewriting the agent path. Consider documenting the current dual-governance model honestly rather than forcing a rewrite.
4. **For C3 (ProviderContract):** Define ProviderContract v1 as a versioned extension of ProviderSpec. Add health, error semantics, evidence requirements. Create conformance suite.
5. **For C4 (Meta):** Verify the spine is actually complete (evidence/spine.py exists, is FROZEN, is used). If yes, formally authorize Meta with a written exception in v4-parking-lot.md.
6. **Tag the release** — only after all gates are green.
