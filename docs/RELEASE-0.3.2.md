# MSB v3 — Release Notes v0.3.2

**Tagged**: 2026-08-27
**Previous**: v0.3.1 (2026-08-17)
**Commits since v0.3.1**: 113
**Total**: 282 source files, 230 test files, ~97K LOC
**Tests**: 2,387 collected, 2,374 pass, 13 skip, 0 fail
**mypy**: 0 errors across 300 source files
**lint**: clean (ruff)

---

## Verdict

```
🟡 PRODUCTION READY WITH DOCUMENTED LIMITATION
```

All automated verification gates are green. DeepSeek provider validation remains
externally blocked pending account credits (C1). The release is production-ready
for the currently validated deployment surface.

---

## What Shipped

### PLEI — Project Lifecycle Engineering Intelligence (Phases 1–7)

The full 7-phase lifecycle intelligence engine:

- **Phase 1**: Project Twin — repository ingestion, architecture extraction, lifecycle classification
- **Phase 2**: Capability Graph — skill taxonomy, role taxonomy, gap detector
- **Phase 3**: Dependency Graph — critical path, failure model, technical debt model
- **Phase 4**: Monte Carlo — simulation, scenarios, sensitivity analysis, forecasting
- **Phase 5**: Decision Engine — prioritization, tradeoffs, next-best-action, provider selection
- **Phase 6**: Harness Integration — decisions route through DeepSeek to providers, governed by MSB
- **Phase 7**: Calibration — prediction → outcome → error → better predictions

180+ tests. 22 API endpoints. Evidence-backed decisions with confidence intervals.

### Speech Pipeline (EXPERIMENTAL)

- Audio capture from file and microphone
- Speech-to-text via openai-whisper (local, no API)
- Speaker verification via resemblyzer
- Intent extraction via pattern matching
- Text-to-speech via macOS `say` + pyttsx3 fallback
- Voice response loop: hear → think → speak

56 tests. Verified end-to-end with real WAV files.
Status: **EXPERIMENTAL** — no API wiring, not production-reachable.

### EnergyMatrix (EXPERIMENTAL)

- System telemetry via psutil (CPU, RAM, disk, thermal)
- Resource scheduler (run / defer / skip decisions)
- Flywheel health bridge integration

21 tests. Integrated with flywheel but no standalone API.
Status: **EXPERIMENTAL** — functional, classified, not promoted.

### Observability & Flywheel (Phases 1–3)

- Prometheus metrics instrumentation across flywheel stages
- Flywheel health bridge with energy-aware scheduling
- CI calibration pipeline with hash-chain integrity
- Evidence spine receipts

58 tests. Production-integrated.

### Closer — Project Closure Engine

- Task DAG with dependency-aware planning
- Gap analysis (critical, verification, debt, optional)
- Online best-practice research and reconciliation
- Evidence requirements per task
- Final closure report with honest completion scoring

### Ops Hardening

- DB schema versioning + structured logging
- Recovery procedure tests (35 cases)
- CI portability fixes
- pytest-xdist parallelization
- 10 mypy errors resolved (0 across 300 files)
- Claims verification gate
- Surface map classification
- Closure drift detection

---

## Known Limitations

| Item | Status | Action Required |
|------|--------|-----------------|
| **C1: DeepSeek API** | 🔴 BLOCKED | Refill credits (human action) |
| **C5: CLI provider sandboxing** | ✅ ACCEPTED | Risk documented; no isolation for sovereign single-machine use |
| **Speech** | 🧪 EXPERIMENTAL | Needs API wiring to promote |
| **EnergyMatrix** | 🧪 EXPERIMENTAL | Needs standalone endpoint to promote |
| **Remote CI** | 🟢 GREEN | All 3 workflows pass |

---

## Production Readiness

```
Engineering Closure:        100% (22/22 tasks)
Automated Verification:     GREEN (mypy, tests, lint, claims, surface)
Code Quality:               GREEN (0 mypy errors, 0 lint errors)
Governance:                 GREEN
Security Posture:           RISKS ACCEPTED (C5 documented)
Experimental Surfaces:      CLASSIFIED (speech, energy_matrix)
External Providers:         C1 CONDITIONAL (DeepSeek billing)
Remote CI:                  GREEN
```

---

## Upgrade Notes

No breaking changes from v0.3.1. All new endpoints are additive.
Existing API surface is unchanged.

---

## Commits (since v0.3.1)

See `git log v0.3.1..v0.3.2` for the full 113-commit history.
