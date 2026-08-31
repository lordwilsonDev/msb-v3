# MSB v3 — Hardening Audit (Wave 2, H1–H15)

<!-- verify-claims: prose-exempt: audit doc — names planned/missing files (H4 broker, H12 local-data) by design while reporting verified state of others -->

**Date:** 2026-08-31 · **Baseline:** v0.4.2 (`8508476`) · **Method:** verified
against real code + `make hygiene` + `tests/chaos/` — not against the roadmap's
assumptions.

**Headline:** the hardening is **~60% done already**, unevenly. The hard parts
(security taint model, failure matrix, reliability, backup) are built and
tested. The gaps are mostly *formalization* (perf baseline doc, RPO/RTO
numbers, SBOM, runbook structure) plus two real missing builds and one wiring
gap.

**Verdict key:** `DONE` (built + tested + evidence) · `PARTIAL` (built, named
gap) · `MISSING` (needs building).

---

## Standing infrastructure the H-phases lean on

- **`make hygiene`** → `scripts/hygiene/hygiene_runner.py --all` — 13 runners:
  h01 load · h02 restart · h03 idempotency · h04 race · h05 contract-fuzzing ·
  h06 audit-tampering · h07 auto-healing · h08 chaos-proxy + runner · h09
  dependency-subtraction · h10 resource-chaos · r01 retrieval-router · r02
  outcome · mutation-score.
- **`tests/chaos/test_failure_matrix.py`** — the 11-mode matrix: model
  unavailable → fail-closed; invalid output → verification fail (not silent);
  tool timeout → task fails + downstream skipped; permission denial → BLOCK +
  audit; corrupted/store failure → degrade never crash; retry exhaustion →
  bounded; **prompt injection → tainted write escalated to REVIEW**;
  conflicting instructions → taint beats approval tier.
- **`ops/`** — `backup.py` (SQLite online-backup, `verify_backup`,
  `restore_backup`, notary snapshot), `verify.py` (before/after state
  snapshots + closed-loop verdicts), `auto_repair.py`, `root_cause.py`.
- **launchd agents** — `msb-backup` (daily 03:00), `db-restore-drill`,
  `disk-health`, `heartbeat`, `replicate`, `ops-audit` (Sunday cascade).
- **CI** — `ci.yml` blocking: `mypy src`, `ruff`, requirement-lock check,
  `pip-audit --strict` CVE gate, claims gate, coverage floor 70.

---

## H1 · Observability — `execution_id` correlation · **PARTIAL**

**Built:** every `handle()` run has a `run_id` threaded through the observation
sink, `TaskLifecycle` events (PLAN_CREATED / TOOL_REQUESTED / TOOL_EXECUTED /
MUTATION_COMMITTED / VERIFICATION_* / EVIDENCE_RECORDED / TASK_*), the evidence
decision-spine (`DecisionEvidence` with `parent_decision_id` chain), and the
UAC audit chain. Prometheus metrics via `observability/metrics.py`.

**Gap:** no single documented artifact that shows one `run_id` linking
`request → plan → auth → tool calls → provider calls → metrics → verification
→ evidence → audit → result` end-to-end. The links exist in code; the
"pull one id, see the whole trace" query/doc does not.

**To close:** a `GET /cockpit/trace/{run_id}` (or doc) that assembles the
chain, + one test asserting every stage carries the same id.

---

## H2 · Performance baseline · **PARTIAL**

**Built:** `scripts/hygiene/h01_load_runner.py`, `tests/benchmark/test_benchmark.py`,
`scripts/soak-run.py`, `observability/soak.py` (SafeProvider + real audit chain
+ scoreboard).

**Gap:** no recorded p50/p95/p99 + throughput + resource-use table for the 8
representative workloads. The measurement tooling exists; the baseline numbers
aren't captured in a doc.

**To close:** run h01 + soak against each workload, record the table.

---

## H3 · Security hardening · **DONE (core) / PARTIAL (coverage doc)**

**Built + tested:** `test_failure_matrix.py` proves the load-bearing invariant
— *LLM-response compromise does not become host compromise*: prompt injection →
REVIEW-gated (the injected instruction cannot drive the write); permission
denial → BLOCK + audit, no execution; taint beats approval tier. Plus
`tests/api/test_mcp_security.py`, `tests/agent/test_safety.py`,
`tests/contracts/test_layered_boundary.py`, h05 contract-fuzzing, h06
audit-tampering. `test_authority_boundary.py` (P3) proves no capability
executes off the gate.

**Gap:** path-traversal, SSRF, and command-injection are handled at the
executor level (`vault_*` executors resolve under a pinned root;
`action_http_call` has a host allowlist) but there is no single threat-model
doc enumerating each class → its specific test.

**To close:** `docs/security/threat-model.md` — one row per class, each citing
its test. Add explicit SSRF + path-traversal cases if any class lacks one.

---

## H4 · Secrets · **PARTIAL → MISSING (broker)**

**Built:** `.env` is `0600` + gitignored; no plaintext secret in the repo
(portability scan + CI); anchor seed in the macOS keychain (not `.env`);
operator token via `scripts/set-operator-token.sh`.

**Missing:** the *broker pattern* — LLM gets a capability reference, a broker
resolves it to the secret, the secret never enters model context. Today a
provider client reads its key from env directly. No test proves a secret
cannot surface via prompt / tool-output / logs / errors / memory / audit / RAG.

**To close:** a `SecretBroker` seam + redaction on the audit/log path + an
exposure test across all 7 channels.

---

## H5 · Automation safety · **DONE**

**Built + tested:** `automation/brain.py` — dry-run by default, creation
requires explicit approval (same rule as cron `requires_approval`),
`BudgetLedger` spend cap, durable `Manifest` (status ∈ created / dry_run /
blocked / failed). h03 idempotency runner +
`test_duplicate_request_reevaluated_independently_no_allow_cache`.

**Residual:** external-operation-ID dedup across retries is implicit (manifest
+ budget), not an explicit idempotency-key on the outbound call. Low risk
given dry-run default. Note in the manifest doc.

---

## H6 · Cron / wake reliability · **DONE**

**Built + tested:** `cron/scheduler.py` — killswitch check, bounded retries,
`asyncio.wait_for` timeout, "recover in-flight" on restart. h02 restart-runner
+ h04 race-runner. `db-restore-drill` launchd agent. Wake cycle is bounded
(`MSB_WAKE_MAX_PER_RUN`, per-turn timeout) and runs under the scheduler's
killswitch.

**Residual:** job *lease* is single-process (in-process scheduler) — fine for
the sovereign single-node runtime; would need a real lease for multi-node.

---

## H7 · Backup / restore · **DONE (mechanism) / PARTIAL (RPO/RTO)**

**Built + tested:** `ops/backup.py` — SQLite online-backup API (consistent
snapshot under live writes), `verify_backup` (checksum), `restore_backup`
(+ notary log). `make backup` / `make restore` / `make backup-verify`.
`msb-backup` daily launchd (7 snapshots, ~4.7G observed). h07 auto-healing.

**Gap:** no stated **RPO** (daily → up to 24h loss) / **RTO**, and no recorded
LIVE → BACKUP → DESTROY → RESTORE → VERIFY drill result covering *all* of
SQLite / config / audit ledger / evidence / Qdrant / memory / operator state
in one pass. `db-restore-drill` covers SQLite.

**To close:** state RPO/RTO in the runbook; run + record the full destroy/restore drill.

---

## H8 · Memory / Storage authority · **PARTIAL**

**Built:** two-authority split is documented (`docs/desktop-architecture.md`
§24): MSB-v3 `memory.py` / `memory_fabric` = execution/evidence/provenance
memory; Obsidian vault = project/decision/knowledge memory. `memory_fabric`
has types (`MemoryType`) + retrieval + verification state.

**Gap:** no single table defining ephemeral / session / project / operator /
system / audit tiers each with owner / schema / retention / access / deletion /
backup / consistency. The `MemoryStore` → `memory_fabric.store` migration is
also unfinished (DeprecationWarning every request).

**To close:** the tier table + finish the `memory_fabric` migration.

---

## H9 · DB migration · **DONE (2026-08-31)**

**Built:** `db/migrations.py` — `Migration(version, sql)`, `ensure_schema()`
applies atomically with rollback, `_schema_version` table, `get_schema_version`,
`list_versions`. 13 tests.

**Closed:** `db/migrations.py` gained `BASELINE` (a no-op-on-existing-data v1
marker migration) + `stamp_all_db(data_dir)` — a directory walk (so new DBs
are covered automatically, no hand-maintained registry) that applies
`BASELINE` + any `REGISTRY` entries to anything below v1, best-effort per DB.
`scripts/stamp-schemas.py` (`--check` = drift report / exit 1; no-arg =
stamp). **All 25 live DBs stamped 0 → 1** (`data/msb_v3.db` 36 278 message
rows, `uac/audit_chain.db` 51 108 audit rows — all intact; only the
`_schema_version` + `_schema_baseline` tables added). `--check` is wired into
`scripts/ops-audit.sh` (Sunday cascade). `tests/db/test_schema_stamping.py` —
7 cases incl. a live-`data/` regression lock that fails if any new store
leaves a DB unstamped. Idempotent + does-not-touch-existing-data proven.

**Residual:** real v2+ migrations are still per-subsystem (add to `REGISTRY`
when a schema actually changes) — that is the correct place for them; the H9
gap was the missing *floor*, now set.

---

## H10 · Supply chain · **PARTIAL**

**Built:** `requirements-runtime.lock` + `requirements-dev.lock` (regenerated
2026-08-25); `ci.yml` "Check requirement locks" is **blocking**; `pip-audit
--strict` CVE gate is **blocking**; `gen-requirements.py --check` fails on
drift.

> **Corrects PRODUCTION-BASELINE.md** — it said "no lockfile in the tree".
> There are two, and a blocking lock+CVE gate.

**Gap:** no SBOM artifact; no license review; reproducible-install is implied
by the locks but not proven by a from-scratch install test.

**To close:** generate an SBOM (`cyclonedx` / `pip-audit --format cyclonedx`)
in CI; a license-scan step; a clean-venv install test.

---

## H11 · Resource governance · **PARTIAL**

**Built:** `scripts/hygiene/h10_resource_chaos_runner.py` (resource-pressure
chaos), disk-health launchd agent (warn 85% / crit 92%).

**Gap:** no documented Mac-mini operating envelope (CPU / RAM / unified-mem /
disk / Qdrant / SQLite / net / model-concurrency) with hard limits, and no
"degrades predictably" assertion across the 8 pressure scenarios.

**To close:** measure the envelope, set limits, extend h10 to assert graceful
degradation per scenario.

---

## H12 · Local data analysis · **MISSING (new feature)**

Not started. The first genuinely-new post-boundary workload: model emits a
constrained plan (JSON), MSB validates, a local executor (DuckDB primary,
Pandas secondary) runs it — model gets no filesystem exec. Deterministic
result vs independent ground truth on a 1M-row set + 12 adversarial inputs.
Full spec in `PRODUCTION-READINESS-ROADMAP.md` H12. ~2 sessions.

---

## H13 · Release / rollback · **PARTIAL**

**Built:** `scripts/release.sh` (runs `verify-release.sh`), the tag-verified
release flow (O2), `release-verify.yml`, portability + pre-push gates.

**Gap:** no DETECT → STOP → ROLLBACK → VERIFY → RECORD path — nothing to roll
`main` / the running service back to the previous tag on a bad deploy, with a
recorded outcome.

**To close:** a `scripts/rollback.sh` (checkout prev tag, restart service,
health-verify, record) + a runbook entry.

---

## H14 · Operator runbook · **PARTIAL — organizational**

**Built:** `docs/ops-runbook.md` (single file) + `docs/operations/` (8 docs:
yubikey/secure-enclave anchors, vesta loopback/shell/wireguard/review). Server
supervision, CI internals, governance, flywheel are in `CLAUDE.md` +
`CLAUDE.archive.md`.

**Gap:** not the roadmap's `docs/ops/` structure (INSTALL / START / STOP /
HEALTH / BACKUP / RESTORE / ROLLBACK / INCIDENT / PROVIDERS / SECRETS /
SECURITY / RELEASE). Content mostly exists, scattered.

**To close:** carve `docs/ops/` from the existing material; fill the 3–4 genuinely
missing pages (ROLLBACK, INCIDENT, PROVIDERS).

---

## H15 · Acceptance matrix · **PARTIAL**

**Built:** `docs/governance/attack-matrix-2026-08-27.md` (safety/resilience,
has UNKNOWN rows); `docs/releases/O3-AUTHORITY-CLOSURE-PLAN.md` (14-path
authority matrix, zero UNKNOWN).

**Gap:** the blueprint §24 21-row production acceptance matrix isn't assembled;
the 8/27 attack matrix still has UNKNOWNs.

**To close:** assemble the 21-row matrix from this audit's evidence; resolve
the 8/27 UNKNOWNs.

---

## Summary

| H | Phase | Verdict |
|---|---|---|
| H1 | Observability correlation | PARTIAL (links exist, no one-id trace artifact) |
| H2 | Performance baseline | PARTIAL (tooling yes, numbers not recorded) |
| H3 | Security hardening | **DONE** core / PARTIAL threat-model doc |
| H4 | Secrets | PARTIAL hygiene / MISSING broker |
| H5 | Automation safety | **DONE** |
| H6 | Cron / wake reliability | **DONE** |
| H7 | Backup / restore | **DONE** mechanism / PARTIAL RPO-RTO |
| H8 | Memory authority | PARTIAL |
| H9 | DB migration | **DONE** — 25 DBs stamped, drift check + regression lock |
| H10 | Supply chain | PARTIAL (locks+CVE yes, SBOM+license no) |
| H11 | Resource governance | PARTIAL |
| H12 | Local data analysis | MISSING (new feature) |
| H13 | Release / rollback | PARTIAL (no rollback path) |
| H14 | Operator runbook | PARTIAL (organizational) |
| H15 | Acceptance matrix | PARTIAL |

**4 DONE, 9 PARTIAL, 2 MISSING.** The PARTIALs are mostly a few hours each of
formalization/wiring; H4-broker and H12 are the real builds. Ordered next
steps: **H9** (wire migrations — concrete, bounded) → **H15 + H3-doc + H7
RPO/RTO + H14** (documentation sweep, evidence already exists) → **H1 trace
artifact** → **H2/H11 measurement** → **H10 SBOM** → **H4 broker** → **H13
rollback** → **H12 local-data**.

`make hygiene` result (2026-08-31, `8508476`): **verdict=pass — 12/12
experiments green** — h01 load · h02 restart · h03 idempotency · h04 race ·
h05 contract-fuzzing · h06 audit-tampering · h07 auto-healing · h08 chaos ·
h09 dependency-subtraction · h10 resource-chaos · r01 retrieval-router · r02
outcome. Artifacts under `artifacts/hygiene/*_20260831T14*.json`.

This is the primary evidence behind the H3 / H5 / H6 **DONE** verdicts and the
H2 / H11 "tooling exists" PARTIALs.
