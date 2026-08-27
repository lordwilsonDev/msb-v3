# MSB-v3 Safety / Resilience Attack Matrix

Date: 2026-08-27
Status: in-progress
Owner: project operator

## Result vocabulary

Every scenario must resolve to exactly one terminal state:

- **PASS** — expected behavior observed.
- **SAFE FAILURE** — request/run rejected, state preserved, evidence recorded, and recovery remains possible.
- **EXPECTED SKIP** — dependency or live mode is explicitly absent and the scenario is outside this gate.
- **SAFE DEGRADATION** — reduced capability is explicit, bounded, and recorded.
- **UNKNOWN** — insufficient evidence; this blocks production closure.

For each attack, record: what was attacked, expected behavior, observed behavior, evidence path, and status.

## Runtime

| Attack | Expected behavior | Observed behavior | Evidence | Status |
|---|---|---|---|---|
| Occupied default port | Run-scoped allocator selects another port without killing owner | CI helper allocates a kernel-selected port and never probes/kills 8766 | `tests/test_ci_runtime.py` | PASS |
| Occupied allocated port | Startup fails clearly or retries with a new owned allocation | Not yet exercised | — | UNKNOWN |
| Startup failure | Explicit failure with captured server log | Helper captures log on early exit | `scripts/ci-runtime.sh` | PASS |
| Runtime crash | Owned PID cleanup runs; no unrelated process is terminated | Not yet exercised end-to-end | — | UNKNOWN |
| Runtime timeout | Bounded failure and owned cleanup | Not yet exercised | — | UNKNOWN |
| Stale PID | Stale PID is not signaled as an owned live process | PID validation is numeric only; process identity test pending | — | UNKNOWN |
| Duplicate cleanup | Cleanup is idempotent | Covered by cleanup implementation/test | `tests/test_ci_runtime.py` | PASS |
| Interrupted cleanup | Trap cleans the owned runtime | Trap is installed after startup; cancellation test pending | — | UNKNOWN |

## Qdrant

| Attack | Expected behavior | Observed behavior | Evidence | Status |
|---|---|---|---|---|
| Unavailable | Required gate reports INFRASTRUCTURE; optional gate skips explicitly | Contract classifies connection failure | `tests/infrastructure/test_qdrant_contract.py` | PASS |
| Unwritable storage | INFRASTRUCTURE with storage diagnostic | Contract hook exists; real storage fault pending | — | UNKNOWN |
| Missing collection | Explicit ENVIRONMENT result | Contract reports missing expected collection | `tests/infrastructure/test_qdrant_contract.py` | PASS |
| Timeout | INFRASTRUCTURE with bounded timeout | Transport failure classification covered | — | UNKNOWN |
| Malformed response | INFRASTRUCTURE, never downstream traceback | Not yet exercised | — | UNKNOWN |

## Model

| Attack | Expected behavior | Observed behavior | Evidence | Status |
|---|---|---|---|---|
| Ollama unavailable | Explicit safe degradation or expected skip | Existing live tests are opt-in/skip-based; consolidated matrix pending | — | UNKNOWN |
| Model missing | Explicit environment failure | Not yet exercised in this matrix | — | UNKNOWN |
| Inference timeout | Bounded failure with evidence | Existing provider tests cover transport boundaries; matrix execution pending | — | UNKNOWN |
| Malformed response | Safe parse failure, no unauthorized action | Not yet exercised | — | UNKNOWN |
| Empty response | Safe failure or explicit degradation | Not yet exercised | — | UNKNOWN |

## Governance

| Attack | Expected behavior | Observed behavior | Evidence | Status |
|---|---|---|---|---|
| Invalid operator token | 401/403; no state mutation | Existing API/security tests cover rejection | `tests/api/test_mcp_security.py` | PASS |
| Unauthorized state change | Denied and audited | Existing governance tests cover bypass boundaries | `tests/governance/test_bypass.py` | PASS |
| ActionGate rejection | BLOCKED; no tool execution | Existing governed-loop tests | `tests/tools/test_governed_tool_loop.py` | PASS |
| Vesta rejection | SAFE FAILURE with audit | Existing Vesta tests | `tests/vesta/` | PASS |
| Budget exhaustion | Bounded stop, evidence retained | Existing governance coverage; matrix execution pending | — | UNKNOWN |
| Kill switch | Immediate block at next governed transition | Existing regression coverage | `tests/agent/test_handle.py` | PASS |
| Governance metadata corruption | Fail closed | Not yet exercised | — | UNKNOWN |

## Evidence

| Attack | Expected behavior | Observed behavior | Evidence | Status |
|---|---|---|---|---|
| Missing event | Verification fails or records an explicit gap | Not yet exercised | — | UNKNOWN |
| Malformed event | Ledger verification fails closed | Existing ledger tests; matrix execution pending | — | UNKNOWN |
| Broken chain | Replay/verification rejects the chain | Existing audit tests; matrix execution pending | — | UNKNOWN |
| Replay mismatch | Replay rejects mismatch | Existing core-loop fixtures document this path | `artifacts/core-loop/` | PASS |
| Missing evidence | Claim verifier fails closed | Existing claim verifier tests | `tests/test_verify_claims.py` | PASS |
| Claim without evidence | Claim rejected | Existing claim verifier tests | `tests/test_verify_claims.py` | PASS |

## Voice

| Attack | Expected behavior | Observed behavior | Evidence | Status |
|---|---|---|---|---|
| Unauthorized speaker | No governed action | Existing speaker tests; promotion execution pending | `tests/speech/test_speech_speaker.py` | UNKNOWN |
| False wake | No action without authorization | Not yet exercised | — | UNKNOWN |
| Transcription failure | Safe failure, no action | Not yet exercised | — | UNKNOWN |
| VAD failure | Safe failure/recovery | Not yet exercised | — | UNKNOWN |
| Barge-in | State remains governed and recoverable | Unit coverage exists; adversarial execution pending | `tests/speech/test_speech_bargein.py` | UNKNOWN |
| TTS failure | Action result is not falsely reported as spoken | Not yet exercised | — | UNKNOWN |
| Voice-triggered unauthorized action | ActionGate/Vesta reject and audit | Safety unit coverage exists; matrix execution pending | `tests/speech/test_speech_safety.py` | UNKNOWN |

## Phase 3 execution notes

- Repository audit found and fixed an unsafe non-CI helper in `scripts/rotate_secrets.py` that discovered and force-killed listeners on `:8766`. Restart is now opt-in through an explicit `MSB_RESTART_COMMAND`; no process discovery is performed.
- Docker is installed locally, but collision execution is not recorded as PASS until a real image build/container run completes and the developer runtime is independently observed before and after.
- The current working tree contains unrelated pre-existing `.plei` and hygiene artifact changes; they remain excluded from this work.

## Exit rule

Production closure cannot be declared while any required scenario is `UNKNOWN`. This artifact is a test plan and evidence ledger, not a claim that the full matrix has passed.
