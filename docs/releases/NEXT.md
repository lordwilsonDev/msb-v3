# MSB v3 — What's Next (handoff, 2026-08-31)

Pick-up doc for a fresh session. Full context lives in the four docs this
points at; read the one for the item you start.

---

## Where things stand

| | |
|---|---|
| `main` | `44b3685` local — **the H9 commit push is in-flight** (pre-push portability ~90% when this was written). |
| `origin/main` | `68d481d` (last confirmed-landed). **First thing: check whether H9 pushed.** |
| Latest tag | `v0.4.2` — `release-verify` green, first CI-verified 0.4.x |
| Runtime | `0.4.2` on `:8766`, launchd-supervised |

### Check H9 push status first

```bash
cd ~/msb-v3
git rev-parse --short origin/main            # 44b3685 = landed; 68d481d = not
git log --oneline -1                          # local HEAD should be 44b3685
```

- **If `origin/main` is `68d481d`** (portability failed or was interrupted):
  the H9 code is committed locally at `44b3685`. Re-push:
  `git push origin main`. If portability flakes on a **live** test
  (`tests/api/test_retrieval_router.py`, `tests/local_ai/...`,
  `tests/triumvirate/...` — `httpx.ReadTimeout`), that's box saturation, not
  the code — restart the server (`bash scripts/start.sh stop && sleep 3 &&
  bash scripts/start.sh start`) and re-push. `tests/db/` (20) + `test-ops`
  (39) + local `-m "not live"` were all green for H9.
- **If it's `44b3685`**: confirm the 3 primary CI gates go green
  (`gh run list --branch main -L4`), then continue below.

---

## Reference docs

| Doc | For |
|---|---|
| `docs/releases/PRODUCTION-READINESS-ROADMAP.md` | the whole backlog, P4→P5→H1–H15, each scoped |
| `docs/releases/HARDENING-AUDIT.md` | verified state of every H-phase (4 DONE / 9 PARTIAL / 2 MISSING) |
| `docs/releases/O3-AUTHORITY-CLOSURE-PLAN.md` | the 14-path authority matrix (P3, done) |
| `docs/releases/PRODUCTION-BASELINE.md` | Phase-0 baseline + drift findings |

---

## Done (do not redo)

- **O1** hermetic `release-verify` — self-provisions, green virgin-clone.
- **O2** release truth — version reconciled to 0.4.2, CI-verified tag.
- **O3 / P3** authority boundary — Option B (dual-governance),
  `test_authority_boundary.py`, 14 paths, zero UNKNOWN.
- **O4** codegraph — key was `"msb-v3"` not a path; re-indexed (7114 nodes).
- **H3** security core, **H5** automation safety, **H6** cron/wake — DONE
  (`make hygiene` 12/12).
- **H9** DB migration — `stamp_all_db` + `scripts/stamp-schemas.py`, all 25
  DBs stamped v1, WAL-checkpointed, ops-audit drift check, regression test.
  *(pending the push landing.)*

---

## Next, in order

### 1. Documentation sweep — evidence already exists, ~1 session

Fastest closure. Each is "write the doc from what's already tested":

- **H15 acceptance matrix** — assemble the blueprint §24 21-row matrix from
  `HARDENING-AUDIT.md` + `O3-AUTHORITY-CLOSURE-PLAN.md`. Resolve the UNKNOWN
  rows in `docs/governance/attack-matrix-2026-08-27.md`.
- **H3 threat-model doc** — `docs/security/threat-model.md`: one row per
  attack class (prompt-inject, tool-inject, path-traversal, SSRF, cmd-inject,
  cred-theft, webhook-spoof, replay, audit-tamper, resource-exhaust) → its
  test. Add SSRF + path-traversal cases if any class has none.
- **H7 RPO/RTO** — state them in the runbook; run + record one full
  LIVE→BACKUP→DESTROY→RESTORE→VERIFY drill covering SQLite + config + audit
  ledger + evidence + Qdrant + memory + operator state.
- **H14 `docs/ops/`** — carve INSTALL/START/STOP/HEALTH/BACKUP/RESTORE/
  ROLLBACK/INCIDENT/PROVIDERS/SECRETS/SECURITY/RELEASE from
  `docs/ops-runbook.md` + `docs/operations/` + `CLAUDE.md`. Genuinely-missing
  pages: ROLLBACK, INCIDENT, PROVIDERS.

### 2. H1 — observability trace artifact, ~1 session

Links exist (`run_id` through lifecycle + evidence spine + audit chain). Build
`GET /cockpit/trace/{run_id}` (or a doc) that assembles request → plan → auth
→ tool calls → provider calls → metrics → verification → evidence → audit →
result, + a test asserting every stage carries the same id.

### 3. H2 + H11 — measurement, ~1 session

- **H2**: run `h01_load_runner` + `soak-run` per representative workload,
  record p50/p95/p99 + throughput + resource use in a table.
- **H11**: measure the Mac-mini envelope (CPU/RAM/unified-mem/disk/Qdrant/
  SQLite/net/model-concurrency), set hard limits, extend
  `h10_resource_chaos_runner` to assert graceful degradation per scenario.

### 4. P4 — provider interchangeability, ~1–2 sessions · **NEEDS CREDENTIALS**

- `tests/contracts/test_provider_failure_matrix.py` — per reachable provider
  (Ollama now; **Claude needs a key confirmed in `.env` / provider registry**;
  DeepSeek blocked on **C1**): normal / structured / tool / timeout / network
  fail / malformed / rate-limit / auth-fail / model-unavailable / fallback /
  recovery.
- Interchange proof: one governed workflow, `resolve_client` pinned to
  Ollama → Claude → DeepSeek, assert governance + canonical lifecycle
  byte-identical.
- **C1**: refill DeepSeek credits → `curl https://api.deepseek.com/v1/models`
  = 200. Consider OpenRouter (one key → many providers) to sidestep.

### 5. P5 — routing measurable, ~1 session

`plei/decisions/provider_selection.py` exists but isn't observable/benchmarked.
Emit a structured routing event keyed to `execution_id`; make dimensions
explicit (privacy/complexity/latency/cost/availability/context/local-pref);
`tests/routing/test_routing_benchmark.py` over a fixed task set.

### 6. H10 — SBOM, ~1 session

Locks + blocking CVE gate already exist. Add: SBOM generation
(`pip-audit --format cyclonedx`) in CI, a license-scan step, a clean-venv
install test.

### 7. H4 — secrets broker, ~1 session · real build

`SecretBroker` seam (LLM gets a capability ref, broker resolves the secret,
secret never enters model context) + redaction on the audit/log path + an
exposure test across 7 channels (prompt/tool-output/logs/errors/memory/
audit/RAG).

### 8. H13 — rollback path, ~1 session

`scripts/rollback.sh` — checkout prev tag, restart service, health-verify,
record. + runbook entry. Nothing to roll back a bad deploy today.

### 9. H8 — memory authority table + finish `memory_fabric` migration

Tier table (ephemeral/session/project/operator/system/audit — owner/schema/
retention/access/deletion/backup/consistency each). Finish the
`MemoryStore` → `memory_fabric.store` migration (kills the DeprecationWarning
on every request).

### 10. H12 — local data analysis, ~2 sessions · biggest real build

New feature. Model emits a constrained plan (JSON), MSB validates, local
executor (DuckDB primary, Pandas secondary) runs it — model gets no
filesystem exec. Deterministic result vs independent ground truth on a
1M-row set + 12 adversarial inputs. Full spec: `PRODUCTION-READINESS-ROADMAP.md`
H12.

---

## Standing gotchas

- **Box saturation** — after a few full suites the machine gets slow and
  live tests (`httpx.ReadTimeout` against `:8766` / `:11434`) flake. Restart
  the server, give it a minute, re-run. These are never real regressions on
  their own — always confirm by running the test in isolation.
- **WAL** — SQLite DBs the live server holds open keep writes in a `-wal`
  sidecar. `ensure_schema` now checkpoints (`wal_checkpoint(TRUNCATE)`); if
  you write a migration/tool that a file-copy must see, checkpoint after.
- **Pre-push hook** runs lint + portability on the **working tree** (~10 min).
  `MSB_SKIP_PORTABILITY=1 git push` is the documented hatch — use it only for
  doc-only commits or when portability's failure is a confirmed live-test
  flake and the real suite is green.
- **Commits** need a `Signed-off-by: lordwilson <theapexintelligence@gmail.com>`
  DCO trailer (commit-msg hook) + `Co-Authored-By: Claude ...`.
- **`.git/index.lock`** occasionally goes stale (0 bytes, no git proc) after
  an interrupted push — safe to `rm -f`.
- **Tag ruleset** `release-tag-immutability` — tags can't be moved/deleted.
  Get the version right before pushing a tag. `v0.4.1` is a dead tag.
