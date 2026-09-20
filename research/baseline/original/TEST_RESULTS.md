# TEST_RESULTS @ 7cb2f7f (R0 candidate)

## Run by this job (FACT — full log in `pytest_full.log`)
Command: `PYTHONPATH=src python -m pytest tests/ -q -x --no-header -p no:cacheprovider` (cwd = repo, python 3.12.9)
Result: **3285 passed, 17 skipped, 75 deselected, 1 warning in 287.53 s**, exit 0. Started 2026-09-20T03:19:18Z, ended 03:24:09Z.
- 75 deselected = the project's pytest config excludes them by default (marker/config not inspected → why they're excluded = UNKNOWN). 17 skips not itemized.
- Only warning: dateutil `utcfromtimestamp` DeprecationWarning (third-party).
- Side effect: the run left `.plei/calibration.jsonl` modified in the tracked tree (see COMMIT.txt). Cause (test vs. concurrent daily job) UNKNOWN.
- Not independent: this is the system's own suite (blueprint §14).

## NOT run by this job
- Coverage measurement (`--cov`), ruff, mypy, pip-audit, the E2E/auth stage, `make hygiene`/factory-gate — status NOT_RUN. To avoid mutating live state (Qdrant collections, launchd server on :8766) I ran no stage beyond pytest.

## Existing gate evidence (FACT that the file says this; not re-verified by me)
`artifacts/hygiene/factory_gate.json` (mtime 2026-09-19 06:22, `VERIFICATION.git_head` = f4b08a8, two commits behind HEAD; later commits are evidence-only chores):
- pytest: 3285 passed, 17 skipped, 75 deselected (417 s) — same counts as my run.
- coverage 84.0 % (floor 65 %, met); live auth: correct→200, wrong→401, missing→401.
- Suite config lists hygiene experiments h01–h10 (load, restart, idempotency, race, contract fuzzing, audit tampering, auto-healing, chaos, dependency subtraction, resource chaos). `daily_gate_events.jsonl` last event 2026-09-19T11:22:29Z: gate_run PASS, unknowns 0.
- RELEASE_VERDICT content: see SYSTEM_BASELINE.md.

## Reading (INFERENCE)
Existing suite is large and green, but nothing in it was designed to answer the blueprint's questions (unauthorized-execution rate, false-verification rate, baselines A/B/C). Green here ≠ any H1–H6 result. h06 (audit tampering) and h05 (contract fuzzing) are the closest existing analogues to blueprint attack classes — worth mining for Benchmark v1 (not inspected).
