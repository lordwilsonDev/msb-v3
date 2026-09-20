# SYSTEM BASELINE — MSB-v3 Research Baseline R0 (**CANDIDATE — awaiting Wilson sign-off; not frozen, not GREEN**)

Repo ~/projects/AI-Agents/msb-v3 @ `7cb2f7f` (main). Produced by JOB-013, worker: claude, 2026-09-20T03:25:09Z.

| Item | Status |
|---|---|
| COMMIT.txt | done; tree was clean at start, .plei/calibration.jsonl dirtied by test run |
| ENVIRONMENT.md | done (secrets not read; env-var NAMES only seen via grep, not listed) |
| ARCHITECTURE.md | done, partial depth: tool path + governance verified; other packages UNKNOWN |
| TEST_RESULTS.md | pytest run (3285 pass); coverage/lint/gates NOT_RUN (cited from prior gate) |
| KNOWN_LIMITATIONS.md | done; I6 (identity) and fail-open audit are the key gaps; all INFERENCE until attack-tested |

Blueprint says Mac mini M1; machine is M4 (Wilson to correct).

## RELEASE_VERDICT (from factory_gate.json, quoted)
```
{
 "critical_requirements_tested": true,
 "critical_invariants_verified": true,
 "critical_failures_resolved": true,
 "security_boundaries_tested": true,
 "state_recovery_tested": true,
 "important_failure_modes_have_experiments": true,
 "claims_have_evidence": true,
 "regression_passed": true,
 "reproducibility_documented": true,
 "live_auth_verified": true,
 "unresolved_unknowns_disclosed": true,
 "release_verdict": "PASS",
 "unresolved_unknowns": [],
 "coverage_floor_met": true,
 "artifact_schema_violations": 0
}
```

## sha256 of files
```
14112c83417615afb69c7da78bcf60b747e1e697d5e5b009bfb0ec97be27e002  COMMIT.txt
24a6ab71b9393c5e526ec84c5e7345f4ec6477f3e5555231997eff5247cadcf5  ENVIRONMENT.md
db684249bef4e04fdf5fc9528485540f40e90112a73420896d25329f7f2360df  ARCHITECTURE.md
61b75ac74a2ed923a9b55824470479af7c2c4792d33cadb67d2814ef5cbe65fd  TEST_RESULTS.md
d619f5af929013f3bb906d41039a4d70c33d403b217e24194f893ed81f9e4d3b  KNOWN_LIMITATIONS.md
17ed6ee2e2f025adf2f3550a6a6e7a7f44c9e75310617f2a1b178744f704e7a7  pytest_full.log
```
