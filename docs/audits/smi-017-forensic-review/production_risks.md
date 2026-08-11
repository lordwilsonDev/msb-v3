# Production Risk Register — SMI-017-v1.0

All findings independently reproduced against the exact frozen tag
(`git checkout SMI-017-v1.0`, commit `d9d8466`) in this worktree. Severity:
CRITICAL = exploitable now, no auth required, real damage. HIGH = broken
trust/claim or wide-open gap. MEDIUM = real but bounded or requires an
uncommon config. LOW = hygiene.

## Fix status as of 2026-08-07 (post-review, on `main`)

- **#1 (registry.py) — FIXED**, commit `a3149a6`. `_entity_path()` now
  resolves and rejects any id whose parent isn't the base dir.
- **#2 (mcp_bridge.py) — FIXED**, commit `3e2928a`. `_normalize_vault_path()`
  added; auth now fails closed instead of allow-all when the secret is
  unset; every file-touching tool call now emits an audit event.
- **#3 (tenants.py) — FIXED**, commit `a3149a6`. Same fix as #1.
- Live server restarted (`scripts/start.sh`, new pid) to pick up all three
  fixes; verified live: `POST /mcp/proxy` without `x-mcp-secret` → `401`,
  with correct secret → `200`, `vault_read` with a `../../` path → `400`.
- Restarting also surfaced and fixed an unrelated bug in the supervisor
  itself: `scripts/run.sh`'s `set -euo pipefail` combined with
  `while true; do cmd; code=$?; done` meant the loop's own "restart on
  crash" never fired — the first non-zero exit killed the supervisor too.
  Fixed in commit `2665902`, verified by killing the child a second time
  and confirming the supervisor respawned it.
- Also fixed: `scripts/run.sh` never exported `MCP_BRIDGE_SECRET` (only
  `MSB_RAG_API_KEY` was wired through), which combined with the new
  fail-closed auth would have 401'd every legitimate request after
  restart. Fixed in commit `dace8d9`.
- **Side effect worth noting:** the restarted server now binds
  `localhost:8766` instead of the previous `*:8766` — the prior process
  had `MSB_HOST=0.0.0.0` set externally, which this restart didn't
  replicate. Less exposed, but reachability from other machines on the
  network changed as an incidental side effect, not a deliberate decision.
- **Not fixed / still open**: #4 (no app-wide auth), #5 (CORS wildcard +
  credentials), #6/#7 (`artifacts/SMI-017/*.json` still hand-authored,
  not regenerated), #8 (test-collection fragility — `pyproject.toml` still
  has no `[build-system]` table), #9 (undeclared `qdrant-client` dep), #10
  (committed runtime state under `data/`), #11 (global unpartitioned kill
  switch — this is exactly what made the restart above riskier than it
  should have been), #12 (tool-loop likely ignores results), #13 (unused
  `pydantic-settings` dep). See `sovereign_agent_factory_phase2.md` for why
  #3/#11's "one global instance" pattern is the real blocker before any
  multi-agent work.

| # | Risk | Severity | Location | Fix |
|---|---|---|---|---|
| 1 | **Path traversal → arbitrary file read/write/delete.** `entity_id` from the request body is interpolated directly into a filesystem path with no sanitization: `_entity_path()` does `Path("data/truth") / f"{entity_id}.json"`. `id: "../../../../etc/cron.d/x"` (or any `../` sequence) escapes `data/truth`. `register`/`retrieve`/`purge` have no auth check at all. | CRITICAL | `src/msb_v3/business/registry.py:20-21,29-42,72-79` | Resolve the path and assert it's still inside the truth dir (`Path.resolve().is_relative_to(base)`); reject ids containing `/`, `\`, or `..`; require an auth dependency on all four routes. |
| 2 | **Path traversal → arbitrary file read/write/append/delete/move against a real personal directory.** `vault_read/write/append/patch/delete/move` join `call.args.get("path", "")` straight onto a hard-coded `/Users/lordwilson/Documents/Vault` with zero sanitization. Auth (`_check_auth`) is a no-op unless the operator sets `MCP_BRIDGE_SECRET`, which is unset by default (`.env.example` has no entry for it). | CRITICAL | `src/msb_v3/api/mcp_bridge.py:16-18,21-26,73-125` | Same path-containment check as #1; make the secret mandatory (fail startup if unset, don't silently allow-all); stop hard-coding a user's home directory — take the vault root from config. **Residual MITIGATED (2026-08-11): vault root is now config-driven (`settings.vault_path` / `MSB_VAULT_PATH`, home-derived default) — the containment root is no longer a machine literal.** |
| 3 | **Same path-traversal pattern, third occurrence.** `_tenant_path(tenant_id)` builds `data/tenants/{tenant_id}.json` from unsanitized input; register/update/delete have no auth. This is now a confirmed systemic pattern (3 independent routers, not one bug). | HIGH | `src/msb_v3/api/tenants.py:19-20,36-57,70-89` | Same fix as #1, applied consistently — consider one shared `_safe_path(base, user_id)` helper instead of three ad hoc copies. |
| 4 | **No authentication on the application.** `api/app.py` mounts 15 routers with no auth dependency anywhere in `create_app()`. Chat, memory, triumvirate control (including the poison-pill kill switch and mission scope lock), business "truth" registry, tenants, and RAG index/search are all open to any caller who can reach the port. The only two auth checks in the entire codebase are opt-in and narrow: `MSB_OPERATOR_TOKEN` (gates one role classification inside `guardian_scanner.py`, not a route guard) and `MCP_BRIDGE_SECRET` (gates one router, off by default). | HIGH | `src/msb_v3/api/app.py:63-123` | Add a FastAPI dependency (e.g. bearer-token check against `settings.operator_token`) applied at `include_router(..., dependencies=[...])` for every non-health route; make it fail-closed if no token is configured, not fail-open. |
| 5 | **Invalid/dangerous CORS configuration.** `allow_origins=["*"]` combined with `allow_credentials=True`. Per the CORS spec browsers reject wildcard-origin-with-credentials responses, but reverse proxies and non-browser clients won't — it signals "no real origin policy was ever decided," and paired with #4 it means any origin can hit an unauthenticated, credentialed API. | HIGH | `src/msb_v3/api/app.py:70-76` | Read `settings.cors_origins` (already defined in `core/config.py:19` and exposed in `/system/config`) into `allow_origins` instead of a hard-coded `"*"`; drop `allow_credentials` unless it's actually needed. |
| 6 | **Shipped release artifact claims a fully green test suite; it isn't.** `artifacts/SMI-017/regression_report.json` states `"total": 208, "passed": 208, "failed": 0, "command": "make test"`, dated the same day as the tag. Running `make test` against this exact frozen checkout produces `1 failed, 207 passed` — `test_least_privilege_allows_matching_scope` fails because `data/triumvirate/poison_pill.json` is **committed to the tag itself** with `"locked_down": true` (dated 2026-08-04, three days before the release commit). Every fresh checkout of "frozen" SMI-017 starts in a kill-switch-engaged state, and the regression artifact does not reflect it. | HIGH | `artifacts/SMI-017/regression_report.json`; state file `data/triumvirate/poison_pill.json` (committed despite `data/` being in `.gitignore` line 4 — confirms it was force-added at some point); root cause in `src/msb_v3/triumvirate/guardian_scanner.py:295-302` (`_is_locked_down()`) | Never commit `data/`; add a pre-release check that runs the exact command in the artifact and fails the release if output doesn't match; reset/regenerate `data/triumvirate/*.json` on a truly frozen checkout, or make `PoisonPill` state test-isolated (inject a tmp path) instead of a fixed path derived from `settings.db_path`. **Partial MITIGATION (2026-08-11):** PoisonPill state is now test-isolated — `tests/triumvirate/conftest.py` redirects pill/SBOM files to a per-session tmp dir, the endpoint cycle test re-arms after detonating (no longer leaves the tree kill-switched), and the committed `poison_pill.json` was reset to unlocked (it was `locked_down: true`; reproduced by the new `make portability` gate, which stages the repo to a temp path and runs the full suite there). |
| 7 | **Shipped security artifact describes controls that don't exist.** `artifacts/SMI-017/security_validation.json` asserts `"auth": "token-map"` and `"findings": ["Centralized auth dependency unchanged"]`. There is no centralized auth dependency anywhere in the codebase (see #4) — the artifact describes a system that was never built, and `"roles": ["[REDACTED]"]` is a literal placeholder string, not real scan output. This and #6 together indicate the `artifacts/SMI-017/*.json` files are hand-authored templates, not generated by any real pipeline (no script in the repo produces them). | HIGH | `artifacts/SMI-017/security_validation.json` | Stop shipping hand-written "validation" artifacts as if they were generated; either build the generator or drop the files — a false attestation is worse than no attestation. |
| 8 | **Test collection is fragile and silently drops files outside `make test`.** Running `pytest` (or `pytest --collect-only`) directly — the default a developer, IDE test-runner, or most CI systems would use — fails to import `tests/api/test_models.py`, `tests/api/test_models_endpoint.py`, `tests/api/test_system_health.py`, `tests/local_ai/test_llama_client.py` (13 tests) with `ModuleNotFoundError: No module named 'msb_v3'`. Root cause: the package is never `pip install -e`'d (no `[build-system]` table in `pyproject.toml`, nothing in CI/dev docs installs it), and the only fix is `Makefile`'s `PYTHONPATH := $(REPO)/src` export plus one file's manual `sys.path.insert(0, str(SRC))` (`tests/test_api.py:11-13`). That file happens to sort alphabetically before the three files it "fixes" by side effect (`tests/api/... < tests/local_ai/... < tests/test_api.py`), so it never helps the very files that fail — bare `pytest` always drops these 13 tests. `pytest-randomly`, `pytest -k`, running a single file, or any CI matrix step that doesn't literally invoke `make test` inherits this silently. | HIGH | `tests/test_api.py:9-13`; `Makefile:8`; `pyproject.toml` (no `[build-system]`) | Make the package pip-installable (`pip install -e .`) and require that in setup docs/CI instead of relying on `PYTHONPATH`/import-order side effects; delete the ad hoc `sys.path.insert` once installation is fixed. |
| 9 | **Undeclared runtime dependency for a just-shipped feature.** `api/rag.py` imports `qdrant_client` behind a `try/except`, but `qdrant-client` is absent from both `pyproject.toml` dependencies and `requirements.lock`. In a clean environment built strictly from the manifest, `/rag/index` and `/rag/search` silently return `501 Qdrant client not installed` instead of failing at install time — this is the most recently landed feature (`d4e7f35`, the commit right before the release tag) and it isn't in the project's own dependency list. | MEDIUM | `src/msb_v3/api/rag.py:14-18`; `pyproject.toml:8-14`; `requirements.lock` | Add `qdrant-client` to `pyproject.toml` dependencies; remove the silent `_HAS_QDRANT` degrade or turn it into a startup-time check with a clear error. |
| 10 | **Runtime/mutable state committed to git.** `data/msb_v3.db` (SQLite binary), `data/memory_graph/*.json`, and all of `data/triumvirate/*` are tracked despite `data/` being in `.gitignore`. Every test run or app run dirties tracked files (`git status --ignored` shows 12+ modified tracked files after a single `make test`), and the "frozen" tag carries stale application state as if it were source (see #6). | MEDIUM | `.gitignore:4` vs actual tracked files under `data/` | `git rm -r --cached data/` once, verify `.gitignore` takes effect, and add a check that fails CI if `git status` is dirty after a clean-room run. **Partial (2026-08-11):** the poison-pill dependency is gone (tests are hermetic; the file is reset to unlocked); the untrack-`data/` step remains open. |
| 11 | **Global, unpartitioned kill switch.** `PoisonPill.detonate()` writes one file (`data/triumvirate/poison_pill.json`) that `GuardianScanner.enforce_least_privilege()` consults for *every* caller, and also calls `MissionAnchor().circuit_breaker_trigger()`, pausing the one global mission for the whole process. There is no per-agent, per-tenant, or per-token scoping — one detonation (accidental, malicious, or a stale committed file as in #6) silently denies every legitimate caller. | MEDIUM | `src/msb_v3/triumvirate/guardian_scanner.py:185-194,227-264` | Scope lockdown state by agent/tenant id rather than one global file; add an explicit re-arm/reset endpoint and alerting when a lockdown is active. |
| 12 | **Multi-step tool loop likely doesn't use tool results.** `LocalAIClient.execute_tool_loop()` builds a `messages` list with tool results appended, but each loop iteration calls `self.generate(query, system=system, tools=tools, ...)` — `generate()` takes a flat `prompt` string, not `messages`, and is called with the *original* `query` every time, not the accumulated conversation. The `messages` list is built and then never consulted by `generate()`. Multi-step tool use (see anything beyond one tool call) is likely non-functional as designed, silently — no test exercises more than one iteration of real tool-result feedback. | MEDIUM | `src/msb_v3/local_ai/ollama.py:88-140` | Switch the loop to use `self.chat(messages, tools=tools, ...)` (which does accept a message list, see `ollama.py:142-173`) instead of `self.generate()`. |
| 13 | **Declared-but-unused dependency.** `pydantic-settings` is a listed dependency in `pyproject.toml`, but `core/config.py`'s own docstring says "no pydantic-settings dependency" and implements `Settings` as a plain dataclass reading `os.getenv`. Minor, but signals dependency-list drift. | LOW | `pyproject.toml:13`; `src/msb_v3/core/config.py:1` | Remove the unused dependency, or actually use it. |

## Test-collection risk detail (referenced by #8)

```
$ pytest --collect-only -q          # bare invocation, no PYTHONPATH
ERROR tests/api/test_models.py
ERROR tests/api/test_models_endpoint.py
ERROR tests/api/test_system_health.py
ERROR tests/local_ai/test_llama_client.py
195 tests collected, 4 errors

$ make test                          # documented entry point
........................................................................ [ 34%]
.....................................................................F.. [ 69%]
................................................................         [100%]
FAILED tests/triumvirate/test_guardian.py::test_least_privilege_allows_matching_scope
1 failed, 207 passed in 3.19s
```

Neither invocation matches the artifact's claimed `208 passed, 0 failed`.
13 tests (6% of the suite) never run at all outside `make test`; even inside
`make test`, one fails on a clean checkout because of committed state (#6).
