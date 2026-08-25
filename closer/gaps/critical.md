# Critical Gaps — Things That Prevent Correct Functioning

## C1: DeepSeek API Key Exhausted (402)
**Impact**: BLOCKS the primary provider seam
**Evidence**: Live test on 2026-08-24 returned 402 on api.deepseek
**Current state**: Fallback chain caught it (paseo.claude succeeded), but the primary provider is dead
**Fix**: Refill DeepSeek API credits
**Verification**: `curl -s https://api.deepseek.com/v1/models` returns 200

## C2: Disk at 91%
**Impact**: BLOCKS multimodal, evidence growth, and long-term operation
**Evidence**: `df -h /` shows 91% on data volume
**Current state**: Documented as Known Limitation #3 — multimodal parked
**Fix**: Clean old artifacts, expand storage, or archive evidence
**Verification**: `df -h /` shows <85%

## C3: No DB Schema Versioning
**Impact**: Data corruption risk at scale
**Evidence**: project-map.md §19, debt_model.py item #1
**Current state**: SQLite with no migration system
**Fix**: Add alembic or similar migration framework
**Verification**: Schema changes apply cleanly to existing databases

## C4: Pre-Push Gate Passes But CI Last Ran 2026-08-20
**Impact**: 25 commits unverified by actual CI
**Evidence**: GitHub Actions last successful run was Aug 20
**Current state**: Local lint/mypy/tests pass, but CI hasn't validated the PLEI subsystem
**Fix**: Trigger CI run, fix any failures
**Verification**: GitHub Actions shows green on main

## C5: CLI Provider Is Best-Effort Isolation, Not a Sandbox
**Impact**: Capability escape surface
**Evidence**: debt_model.py item #2, L9 parked
**Current state**: Provider runs in same process space
**Fix**: Process isolation or container-based execution
**Verification**: Security test confirms no capability escape

## C6: Port Conflict on :8080
**Impact**: Blocks llama-server / local AI
**Evidence**: moie-os process occupies :8080
**Current state**: settings.llama_cpp_url defaults to :8080
**Fix**: Move moie-os or change LLAMA_CPP_URL
**Verification**: `curl http://localhost:8081/health` returns 200 (or whatever port is chosen)
