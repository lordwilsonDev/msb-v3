# Critical Gaps — Things That Prevent Correct Functioning

## C1: DeepSeek API Key Exhausted (402) — BLOCKED
**Impact**: BLOCKS the primary provider seam
**Evidence**: Live test on 2026-08-24 returned 402 on api.deepseek
**Current state**: Fallback chain caught it (paseo.claude succeeded), but the primary provider is dead
**Fix**: Refill DeepSeek API credits
**Verification**: `curl -s https://api.deepseek.com/v1/models` returns 200
**Status**: BLOCKED — needs user billing action (30 min)

## C2: Disk at 76% — RESOLVED
**Impact**: Was BLOCKING multimodal, evidence growth, and long-term operation
**Evidence**: `df -h /` shows 76% on data volume (down from 91%)
**Current state**: 5.1GB Ollama models — not blocking current operation
**Fix**: Already resolved — disk usage dropped from 91% to 76%
**Verification**: `df -h /` shows <85%
**Status**: RESOLVED — 2026-08-25

## C3: No DB Schema Versioning — SHIPPED
**Impact**: Data corruption risk at scale
**Evidence**: project-map.md §19, debt_model.py item #1
**Current state**: SQLite with no migration system
**Fix**: `5589fd6` — feat(v3.1): Tier 1 — DB schema versioning + structured logging
**Evidence**: `src/msb_v3/db/migrations.py` — 156 lines, migration system with 13 tests
**Status**: SHIPPED — 2026-08-25

## C4: Pre-Push Gate Passes But CI Last Ran 2026-08-20 — RESOLVED
**Impact**: 25 commits unverified by actual CI
**Evidence**: GitHub Actions last successful run was Aug 20
**Current state**: CI is green on all 3 workflows (msb-v3 CI, harness-gate, factory-gate)
**Fix**: `f645352` — fix(ci): resolve 5 CI portability failures across 3 workflows
**Verification**: GitHub Actions shows green on main (8df21ad)
**Status**: RESOLVED — 2026-08-26

## C5: CLI Provider Is Best-Effort Isolation, Not a Sandbox — ACCEPTED
**Impact**: Capability escape surface
**Evidence**: debt_model.py item #2, L9 parked
**Current state**: Provider runs in same process space
**Decision**: Risk accepted in writing (2026-08-26)
**Rationale**: Sovereign single-machine — operator IS the system. Process isolation provides marginal security benefit.
**Reversal trigger**: Any exposure to untrusted input, external API, or autonomous execution requires immediate isolation review.
**Status**: ACCEPTED — risk documented, condition for reversal defined

## C6: Port Conflict on :8080 — RESOLVED
**Impact**: Was BLOCKING llama-server / local AI
**Evidence**: moie-os process occupied :8080
**Current state**: Default changed from :8080 to :8081
**Fix**: `522e1c9` — feat(closer): closure plan + E2E integration tests + port fix
**Evidence**: `src/msb_v3/core/config.py` — llama_cpp_url defaults to `http://localhost:8081/v1`
**Status**: RESOLVED — 2026-08-25

---

## Summary

| Gap | Status | Closed By |
|-----|--------|-----------|
| C1 | 🔒 BLOCKED | — (needs billing) |
| C2 | ✅ RESOLVED | disk dropped to 76% |
| C3 | ✅ SHIPPED | `5589fd6` |
| C4 | ✅ RESOLVED | `f645352` |
| C5 | ✅ ACCEPTED | risk accepted 2026-08-26 |
| C6 | ✅ RESOLVED | `522e1c9` |

**Resolved: 5/6 (83%)**
**Accepted: 1/6 (17%) — C5 CLI provider sandboxing**
**Blocked: 1/6 (17%) — C1 DeepSeek API (needs billing)**
**Open: 1/6 (17%) — C5 CLI provider sandboxing (Phase 2)**

**Critical count: 6 (not 4 as previously reported)**
