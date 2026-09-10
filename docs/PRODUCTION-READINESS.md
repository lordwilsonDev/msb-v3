# MSB v3 — Production Readiness Report

**Generated**: 2026-09-02
**Version**: 0.4.2
**Engineer**: Buffy (automated convergence + hardening pass)

---

## Production Readiness Verdict

```
🟡 PRODUCTION READY WITH DOCUMENTED LIMITATION
```

MSB-v3 has a strong verified local core, but the current close-out gate is conditional: all non-Docker legs pass while Docker verification is blocked because the local Docker daemon is unavailable. DeepSeek/frontier provider validation remains externally blocked pending account credits (C1). The release is therefore ready only for the currently validated local deployment surface, subject to these documented limitations.

---

## Four Independent States

| State | Status | Evidence |
|-------|--------|----------|
| **Engineering Closure** | 100% | 22/22 tasks done, closure report updated |
| **Automated Verification** | GREEN | 3,175 tests pass, 14 skipped in portability mode; mypy 0 errors; lint, claims, policy, hygiene, smoke, and portability pass |
| **External Provider Verification** | CONDITIONAL | C1 (DeepSeek) blocked on billing — no provider calls verified |
| **Production Readiness** | CONDITIONAL | Local validated surface; Docker close-out blocked; subject to C1 and documented limitations |

---

## Gate Matrix

| Gate | Result | Evidence |
|------|--------|----------|
| **Tests** | 🟢 GREEN | 3,175 passed, 14 skipped, 0 failed in portability verification |
| **Mypy** | 🟢 GREEN | 0 errors across 366 source files |
| **Lint** | 🟢 GREEN | ruff check clean |
| **Surface** | 🟢 GREEN | 4/4 surface map tests pass, 194 routes classified |
| **Convergence** | 🟢 GREEN | 100% engineering closure (22/22 tasks) |
| **Secrets** | 🟢 CLEAN | No hardcoded credentials, .env.example has empty placeholders |
| **Security** | 🟡 ACCEPTED | C5 risk accepted in writing, threat model documented |
| **C1** | 🔴 CONDITIONAL | DeepSeek API blocked — no provider verification possible |
| **C5** | 🟢 ACCEPTED | CLI provider sandboxing risk accepted, reversal trigger defined |
| **CI** | 🟡 CONDITIONAL | Local lint/test/hygiene/portability pass; close-out Docker leg blocked by unavailable Docker daemon |

---

## What Was Fixed

| Fix | Commit | Impact |
|-----|--------|--------|
| mypy 10 errors → 0 | 9ab7d41 | Type safety across 282 files |
| Surface map updated | d58485e | speech + energy_matrix classified |
| Closure report updated | dd32a8d | V5/V6/V8 marked resolved, 98% → 100% |
| C5 decision recorded | acc6b88 | Risk accepted in writing |
| Stale critical.md updated | (this session) | C5 status corrected |

---

## What Was Verified

| What | How | Result |
|------|-----|--------|
| Type checking | `mypy src/msb_v3` | 0 errors |
| Test suite | `make test` and `make portability` | 3,175 pass, 14 skipped in portability mode |
| Lint | `ruff check src/ tests/` | Clean |
| Surface consistency | `pytest tests/docs/test_surface_map.py` | 4/4 pass |
| Secrets hygiene | Manual audit | No hardcoded credentials |
| Code execution surfaces | Manual audit | subprocess behind ActionGate, no shell=True, no eval |
| Audit logging | Manual audit | 137 audit references, hash-chained |
| Route surface | Manual audit | 194 routes, all classified |
| New subsystems | Manual audit | speech + energy_matrix classified as EXPERIMENTAL |

---

## What Was Not Verified

| What | Why | Impact |
|------|-----|--------|
| **DeepSeek API** | C1 — billing/credits required | Provider integration unverified |
| **Docker close-out** | Docker daemon unavailable at `/Users/lordwilson/.docker/run/docker.sock` | Container build and `/health` smoke remain unverified |
| **Performance under load** | No load test run | May have resource issues at scale |
| **Multi-user scenarios** | Single-operator design | Not tested with concurrent users |
| **Disaster recovery** | Backup/restore exists but not tested end-to-end | May have edge cases |
| **Speech pipeline production use** | No API wiring | EXPERIMENTAL — not production-reachable |
| **Energy Matrix standalone use** | No standalone endpoint | EXPERIMENTAL — only reachable via flywheel |

---

## Threat Model Summary

| Surface | Trust Level | Control | Residual Risk |
|---------|-------------|---------|---------------|
| **Tool execution** | Operator-controlled | ActionGate + verification | Low — governed |
| **CLI provider** | Same process | C5 risk accepted | Medium — no isolation |
| **Subprocess** | Operator-controlled | ActionGate + kill switch | Low — governed |
| **RAG/Qdrant** | Internal | Tenant isolation | Low |
| **SQLite** | Local file | Baseline schema stamping + migration registry | Low-to-medium — subsystem v2+ migrations remain per registry |
| **Audit ledger** | Hash-chained | Tamper-evident | Low |
| **FastAPI routes** | Operator-authenticated | Token-based auth | Low |
| **Environment vars** | Local | .env not committed | Low |
| **Logging** | Structured | No secrets in logs (verified) | Low |

---

## Security Decisions

### C5: CLI Provider Sandboxing

- **Decision**: Risk accepted in writing
- **Date**: 2026-08-26
- **Rationale**: Sovereign single-machine — operator IS the system. Process isolation provides marginal security benefit for the current deployment model.
- **Reversal trigger**: Any exposure to untrusted input, external API, or autonomous execution requires immediate isolation review.
- **Note**: Risk acceptance is a deployment decision, not proof that sandboxing has no security value.

---

## Experimental Systems

### Speech Pipeline (`speech/`)

| Field | Value |
|-------|-------|
| Status | EXPERIMENTAL |
| Tests | 56 |
| Integration | None (no API wiring) |
| Production reachability | None |
| Known limitations | No API endpoint, no auth gate, no real workflow |
| Promotion criteria | Needs `/speech/command` endpoint + auth gate + real workflow |
| Security considerations | Microphone access, speaker verification data |

### Energy Matrix (`energy_matrix/`)

| Field | Value |
|-------|-------|
| Status | EXPERIMENTAL |
| Tests | 25 |
| Integration | Flywheel health bridge (reads telemetry) |
| Production reachability | Indirect (via flywheel health check) |
| Known limitations | No standalone endpoint, no operator tooling |
| Promotion criteria | Needs `/energy/status` endpoint + documented budgets |
| Security considerations | System resource monitoring — low risk |

---

## C1: DeepSeek API

**Status**: BLOCKED

**Why**: Account credits exhausted. Provider returns HTTP 402.

**What remains unverified**:
- Authentication flow
- Provider reachability
- Request/response cycle
- Error handling (timeout, rate limit, malformed response)
- Credential exclusion from logs
- Governed execution path integration

**Verification procedure** (when credits restored):
```bash
# 1. Verify API key
curl -s https://api.deepseek.com/v1/models | jq .

# 2. Verify governed path
curl -X POST http://localhost:8766/agent/run \
  -H "Authorization: Bearer $MSB_OPERATOR_TOKEN" \
  -d '{"prompt": "test", "provider": "deepseek"}'

# 3. Verify error handling
# - Check logs for credential leakage
# - Verify timeout behavior
# - Verify rate-limit handling
```

**C1 remains externally unverified until credits are restored and the above procedure passes.**

---

## Release Recommendation

```
TAGGED WITH LIMITATION
```

**Reasoning**:

1. All local verification gates pass (mypy, tests, lint, surface, convergence)
2. Engineering closure is 100%
3. Secrets audit is clean
4. Threat model is documented
5. C5 risk is accepted with reversal trigger
6. Experimental surfaces are classified
7. C1 is the only external limitation

**The current `v0.4.2` release line should retain release notes that explicitly document:**
- C1 limitation (DeepSeek unverified)
- C5 acceptance (CLI provider sandboxing)
- Experimental status (speech, energy_matrix)
- What was verified vs what was not

**Do not release as "production-ready" without the C1 caveat.**

---

## Acceptance Checklist

- [x] mypy = 0 errors
- [x] lint = GREEN
- [x] required tests = GREEN (3,175 pass, 14 skipped in portability mode)
- [x] claims verification = GREEN (closure report updated)
- [x] surface verification = GREEN (4/4 pass)
- [x] convergence = GREEN (100%)
- [x] secrets audit = CLEAN
- [x] experimental surfaces = CLASSIFIED
- [x] C5 risk = DOCUMENTED
- [x] C1 limitation = DOCUMENTED
- [x] audit/logging = REVIEWED
- [x] provider failure paths = REVIEWED
- [x] persistence = REVIEWED
- [ ] working tree = CLEAN (generated calibration/hygiene evidence plus audit/scratch files remain local)
- [x] release documentation = TRUTHFUL
- [ ] Docker close-out = BLOCKED (Docker daemon unavailable)
- [ ] Remote CI = NOT TESTED (requires push)

**Local engineering gates are green; Docker close-out and remote CI remain conditional.**
