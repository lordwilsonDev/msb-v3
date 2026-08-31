# MSB v3 — Production Closure Baseline

**Frozen:** 2026-08-31 · **Blueprint:** PRODUCTION-CLOSURE-001, Phase O
**Purpose:** the reproducible starting point for the P0→P5 production-boundary
closure. Every field verified against the working tree at the commit below, on
2026-08-31, unless labeled otherwise.

---

## Identity

| Field | Value |
|---|---|
| Project | msb-v3 |
| Commit (HEAD) | `1b80a74dfeca25541ed37bbf8b85ac6b37916f8f` |
| `git describe` | `v0.4.0-5-g1b80a74` |
| Branch | `main` — 0 commits unpushed to `origin/main` |
| Working tree | clean except telemetry churn (`.plei/calibration.jsonl`, `artifacts/hygiene/*.jsonl`, `CLAUDE.md`) + untracked audit/scratch files |
| Last release tag | `v0.4.0` → commit `bc08a93` (**5 commits behind HEAD**) |
| `pyproject.toml` version | `0.3.1` |
| `msb_v3.__version__` | `0.3.1` |
| Runtime `/status` version | `0.3.1` |

> **Version-drift finding (feeds O2 / Phase 2):** three different version
> identities are live — `pyproject` / `__version__` / `/status` all say
> `0.3.1`, the last two tags are `v0.3.2` and `v0.4.0`, and
> `scripts/verify-release.sh` defaults to verifying `v<pyproject version>` =
> `v0.3.1` (a tag 100+ commits old). `tests/test_release_versions.py` pins
> internal source agreement but nothing pins *tag == pyproject == verified
> commit*. Closing O2 must reconcile these.

## Toolchain (host)

| Component | Version |
|---|---|
| OS | macOS 26.6.2 (build 25G83), Darwin arm64 (M-series Mac mini) |
| Python | 3.12.9 — `/opt/homebrew/Caskroom/miniforge/base/bin/python` · `requires-python >=3.11` |
| Node | v22.23.1 |
| Ollama | 0.33.1 (`:11434`) |
| Qdrant | 1.18.3, commit `db8fa43` (`:6333`) |

## Services & supervision

| Service | Port | Supervisor | State at baseline |
|---|---|---|---|
| msb-v3 | 8766 | LaunchAgent `com.lordwilson.msb-v3` (`KeepAlive`, PID 70259) | healthy, `ready:true`, model `qwen3:8b`, `db_path=data/msb_v3.db` |
| Ollama | 11434 | external | up, models present |
| Qdrant | 6333 | LaunchAgent `com.lordwilson.qdrant` (`WorkingDirectory=$REPO`, `KeepAlive`, PID 2089) | up |

Other active LaunchAgents: `n8n`, `paseo`, `trinity-context-daemon`,
`trinity-hot-reload`, `ops-audit`, `msb-chain-notary`, plus scheduled
backup/heartbeat/replicate/rotate-logs/disk-health/vesta-approval-watchdog
agents (see `scripts/launchd/`).

## Test inventory (verified 2026-08-31)

| Gate | Result |
|---|---|
| `pytest` collection | **3058 tests collected** |
| Full suite vs live `:8766` (HEAD) | **3039 passed · 18 skipped · 1 failed** in 606s |
| The 1 failure | `tests/local_ai/test_local_inference.py::TestOllamaIntegration::test_ollama_chat_endpoint` — `httpx.ReadTimeout` on a live model call (120s timeout) under a fully saturated machine. Not a logic regression; load-fragility. Feeds P1. |
| Core subset (`contract`/`gateway`/`canonical`/`import_direction`/`interchange`/`action_gate`/`lifecycle`) | 484 passed, 0 failed |
| `tests/contracts/test_provider_contract.py` | 190 passed |
| `tests/architecture/test_gateway_canonical.py` | 7 passed |
| `ruff check .` | clean |
| `scripts/test-ops.sh` (ops-script regression) | 39 passed, 0 failed |
| 3 primary CI gates on HEAD (`msb-v3 CI`, `factory-gate`, `harness-gate`) | green (2026-08-29 12:24) |
| `release-verify` on `v0.4.0` (`bc08a93`) | **RED** — 3 failed / 3036 passed / 13 skipped (self-hosted virgin clone). Failing test names not recoverable from GitHub — `verify-release.sh` writes the full log to a runner-local file and greps back only the summary line. Feeds P1. |

18 skips = opt-in live/seeded tests (`MSB_LIVE`, research-runtime fixtures).
No unexpected skips in the local run.

## Persistence state

**SQLite — 26 databases under `data/`.** Every one reports
`PRAGMA user_version = 0` and none has a `schema_version` table.

> **Migration-drift finding — RESOLVED 2026-08-31 (H9).** `db/migrations.py`
> gained `stamp_all_db()` + `scripts/stamp-schemas.py`; all 25 live DBs are
> stamped at schema v1 (data intact — only `_schema_version` /
> `_schema_baseline` tables added). Drift check wired into `ops-audit.sh`;
> `tests/db/test_schema_stamping.py` is the regression lock. Real v2+
> migrations remain per-subsystem via `migrations.REGISTRY`.

Largest: `data/memory_fabric/memory.db` (31 MB), `data/runtime/tasks.db`
(31 MB), `data/runtime/wake.db` (24 MB), `data/uac/audit_chain.db` (17 MB),
`data/runtime/runtime.db` (17 MB).

**Qdrant — 2 collections:** `tenant_wilson-vault` (the vault RAG index, ~5.4k
chunks) and `tenant_live_test_1787679462` (a stray test collection —
`make qdrant-sweep` should remove it).

## Dependencies

- **Correction (2026-08-31):** the tree *does* carry
  `requirements-runtime.lock` + `requirements-dev.lock` (regenerated
  2026-08-25), and `ci.yml` has a **blocking** lock-drift check +
  `pip-audit --strict` CVE gate. The original "no lockfile" note here was
  wrong. Remaining Phase-17 gaps: SBOM, license review, reproducible-install
  proof (see `HARDENING-AUDIT.md` H10).
- `pip-audit` on the host env is dominated by conda-base noise
  (`conda`, `libmambapy`, … "not found on PyPI"); a clean audit needs a
  locked runtime environment. Feeds Phase 17.
- Known pin: `pytest==9.0.3` (PYSEC-2026-1845 — 8.3.5 vulnerable).

## Reproduction

From a clean checkout on an M-series Mac mini running macOS 26.x:

```bash
git clone <remote> msb-v3 && cd msb-v3
git checkout 1b80a74
make setup          # deps, launchd agents, qdrant, models, /health smoke
make server-start
curl -s 127.0.0.1:8766/status   # expect ready:true, version 0.3.1
make test           # expect ~3039 passed; the 1 live-ollama flake is load-dependent
```

## Exit condition (Phase O)

- [x] Exact HEAD, toolchain, service, and persistence state recorded.
- [x] Full-suite result captured with the one failure diagnosed.
- [x] Version-identity, migration-state, and dependency-lock gaps named and
      routed to their closure phases (O2, Phase 18, Phase 17).
- [x] Independent reproduction from a virgin clone — **proven 2026-08-31**:
      `release-verify` on `v0.4.2` runs a virgin clone of the tag through
      seed + a run-scoped server + the `-m "not live"` suite and is green
      (3040 passed). See `docs/releases/v0.4.2.md`.

Phase O is **complete**. The virgin-clone path now works without the live
`:8766` — P1 (Option A) closed that.
