# R0 addendum — gaps closed after independent review (2026-09-20)

**Status: CANDIDATE. Not frozen. Not GREEN.** The Evidence Authority (Wilson) freezes R0 or does not. This addendum closes the gaps an independent read-only review found in the R0 candidate (`original/`, produced by JOB-013 at `7cb2f7f`). **`original/` is a byte-identical copy of that candidate; nothing in it was edited.** Corrections live here so the original stays what was reviewed.

**Baseline subject:** commit `7cb2f7f9ca669fd0723c9fa3a54708710accea34`. All re-runs below were done in an isolated `git worktree` at that commit (not the working tree), Python 3.12.9, Apple M4 / 16 GiB / macOS 26.6.2, with `pytest==9.0.3` supplied from a side directory so the host environment was not modified. HEAD at the time of this addendum is later (`e3e91fe`, 13 jobs of shadow-mode/hardening work) — **none of that is part of R0.**

## Gap closure table
| # | Gap | Result | Evidence |
|---|---|---|---|
| 1 | Environment ≠ lockfile (pytest 8.3.4 recorded; `pyproject.toml:27` pins 9.0.3) | **CONFIRMED and re-run under 9.0.3.** The host env runs 8.3.4, which is the version `pip-audit` flags (PYSEC-2026-1845, fixed in 9.0.3) — that is *why* the project pins 9.0.3. Under 9.0.3: **3286 passed, 19 skipped, 75 deselected, 1 failed (see note A)**. The original run (8.3.4, `tests/` only) is unchanged. | `evidence/pytest9_summary_at_7cb2f7f.txt`, `evidence/pip_audit_declared_deps.txt` |
| 2 | Coverage / factory-gate not run at `7cb2f7f` | **Coverage now measured at `7cb2f7f`: 84% (29,018 statements, 4,626 missed)** — matches the 84.0% R0 previously *quoted* from the older gate at `f4b08a8`, so the quote held. Factory-gate as a whole (hygiene battery, live auth, E2E) was **not** re-run — it needs the live service and mutates state; still UNKNOWN for `7cb2f7f` itself (the daily gate's own PASS for that commit is `7cb2f7f`'s commit message, i.e. self-reported). | `evidence/coverage_by_file_at_7cb2f7f.txt` |
| 2b | ruff / mypy / pip-audit never run at `7cb2f7f` | **ruff clean (`src tests` and `.`); `mypy src` clean (372 files).** pip-audit: **declared dependencies: 1 vulnerable package — pytest 8.3.4 (host env), fixed by the pinned 9.0.3.** Whole-host-env audit (841 packages, mostly unrelated conda packages) lists 400+ advisories — recorded as a superset, **not** the project's dependency surface; do not read it as "the project has 400 vulnerabilities". Declared-deps audit covers direct dependencies only (18 of 20 names were pinned in the env), so it is a lower bound on the transitive surface. | `evidence/ruff_mypy_at_7cb2f7f.txt`, `evidence/pip_audit_*.txt` |
| 3 | Config and env-var names not captured | **Captured, names only.** 80 names in `.env.example` + names referenced in `src/` but not declared there. The live `.env` was not read (lane rule); whether a name is *set* on the host is deliberately not recorded. Config = `pyproject.toml` tool sections and declared dependencies. | `evidence/ENV_VAR_NAMES.txt`, `evidence/CONFIG_SNAPSHOT.md` |
| 3b | Full pip freeze not saved | **Saved (host environment, 884 lines incl. 43 conda `@ file://` entries — it is the host, not a project lock).** Only `name==version` lines were auditable. | `evidence/pip_freeze_host_env.txt`, `evidence/pip_declared_deps_pinned.txt` |
| 4 | Second `testpaths` entry not run; 75 deselected unexplained | **Both `tests/` and `src/personal_intelligence/tests` now run** (the count rose by 4 collected tests). The 75 deselected are the tier-marked tests (`integration` = needs a live msb-v3 on :8766, `live` = real Ollama, `chaos` = fault-injection proxy subprocess), run only with `MSB_RUN_TIERS=1` (as CI does; `tests/conftest.py` explains why they are deselected by default). **Recorded as NOT RUN here, on purpose:** per that comment, `test_cold_state_verification.py` SIGKILLs whatever owns tcp:8766 and re-spawns it, which would restart the live service. So R0's "UNKNOWN" is resolved to: *deselected by marker, unrun.* | `evidence/pytest9_summary_at_7cb2f7f.txt`; `original/TEST_RESULTS.md` |
| 5 | Dirty tree and "cause UNKNOWN" for `.plei/calibration.jsonl` | **Cause resolved (FACT, observed repeatedly on 2026-09-20):** the tracked file is appended to by test runs and by the running service; it reappears modified after every suite run and `git checkout` reverts it. It is runtime noise, not a source change. The pre-R0 stash (`stash@{0}` "pre-R0 clean 2026-09-19": `.plei/calibration.jsonl`, `artifacts/hygiene/daily_gate_events.jsonl`, plus the items listed in `original/COMMIT.txt`) still exists and is **untouched**. | this file |
| 6 | Citation `runtime.py:148` | **Errata: `executor(...)` is at `runtime.py:149`** (l.148 is the audit line before it; verified with `git show 7cb2f7f:src/msb_v3/tools/runtime.py`). | — |
| 7 | I1 "UNKNOWN" and I6 over-statement | See "Invariant row corrections" below. | — |
| 8 | Independence | **Not closable here and not claimed.** Every number in R0 comes from the system's own tests and gates, run by AI agents. The gate verdicts/coverage are the system grading itself (blueprint §14 caveat). Independent scrutiny is a later gate (blueprint §24), not something an addendum can supply. | — |
| 9 | `SYSTEM_BASELINE.md` unhashed | **`CHECKSUMS.sha256` added** covering every file under `research/baseline/` including the original copies. | `CHECKSUMS.sha256` |

**Note A — the one failure is a harness artifact, recorded, not hidden.** `tests/db/test_schema_stamping.py::test_live_data_dir_is_fully_stamped` failed in the isolated worktree under **both** pytest 8.3.4 and 9.0.3, run alone. The test asserts the *live* `data/` DBs are schema-stamped; a fresh worktree has an empty `data/` populated by the run itself (the test's own `MSB_CI` skip comment names this case). It is a property of where the test ran, not of pytest's version or of `7cb2f7f`'s code. It passed in the original run (in the live checkout) and in the later full-suite runs at HEAD. It was not "fixed"; the count above includes it as a failure.

## Invariant row corrections (KNOWN_LIMITATIONS.md, FACT read at `7cb2f7f`)
- **I1 (authorization) — "UNKNOWN" is answerable.** The registry has 19 `ToolDef`s; **none sets `approval_required=True`**, so the approval check at `runtime.py:133` cannot fire on this path. **10 of 19 have empty `required_capabilities`** (e.g. `search_vault`, `codegraph.rename`, `moie.analyze`), so they execute with no capability check at all. (Same finding as spec Del 02 §14.1; here independently reproduced.)
- **I6 (identity) — "no identity" is true only for the tools path.** `agent/identity.py` (`AgentIdentity`, fingerprint, grants) exists and is used in `agent/handle.py` (the DAG path). `_run_governed` has no actor notion and takes `granted`/`approved` from the request context (`runtime.py:172-173`). The precise claim: *the tool path has no identity check; the DAG path resolves one.*
- **I4 — audit fail-open** confirmed (`runtime.py` l.44-46 "never fatal", l.67-68 `except Exception: logger.debug`).

## What is still NOT closed
1. Factory-gate as a whole at `7cb2f7f` (needs the live service; self-reported PASS only).
2. The 75 tier tests (unrun, by design).
3. Independence (see #8).
4. **The freeze itself** — that is Wilson's. Recommended: freeze **at `7cb2f7f`** and label everything after it *post-R0* (blueprint §5's point is a fixed reference).

## Reproduce
```bash
git worktree add --detach /tmp/r0 7cb2f7f && cd /tmp/r0
pip install --no-deps --target /tmp/pytest9 pytest==9.0.3 pluggy iniconfig packaging
PYTHONPATH=/tmp/pytest9:$PWD/src python -m pytest -p no:cacheprovider --cov=msb_v3 --cov-report=term -q   # ~7 min
ruff check src/ tests/ && ruff check . && mypy src
```
