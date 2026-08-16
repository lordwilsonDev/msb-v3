# JSON State-Store Inventory — msb-v3 (2026-08-16)

Phase 1.3 of the completion blueprint. Inventories every **persistent JSON
file that source code reads/writes** and classifies it so we know which ones
are "filesystem-as-database" debt and which legitimately stay file-based.

Method: `find` for `*.json`/`*.jsonl` + `grep` for `.json` literals and
`json.load/dump`/`read_text`/`write_text` across `src/msb_v3`. Paths verified
against source; env overrides noted where they exist.

## Taxonomy

| Class | Meaning | Default disposition |
|---|---|---|
| CONFIG | Static settings / external user data, read at startup | keep file-based |
| CACHE | Derived, recomputable, safe to delete | keep file-based |
| EPHEMERAL | Transient per-session/per-run data; client-local credentials | keep file-based |
| STATE | Durable authoritative state that must survive restart and be mutation-controlled | **migrate to SQLite** |
| AUDIT | Append-only, tamper-evident records (chain anchor, ledgers) | keep file-based |
| EVIDENCE | Per-run / per-mission output artifacts | keep file-based |

## Master inventory (source-backed)

### STATE — "filesystem-as-database" (migrate to SQLite)

| # | Module | Files | Notes |
|---|---|---|---|
| 1 | `triumvirate/mission_anchor.py` | `data/triumvirate/STATUS.json` | mission status engine — single source of truth for "what state is this mission in" |
| 2 | `triumvirate/guardian_scanner.py` | `data/triumvirate/sbom_registry.json` | per-tool capability registry |
| 3 | `triumvirate/guardian_scanner.py` | `data/triumvirate/poison_pill.json` | kill-switch marker; overlaps `governance/killswitch.py` (SQLite) — consolidation candidate |
| 4 | `triumvirate/hardware_sovereignty.py` | `data/triumvirate/mesh_state.json` | peer-node mesh membership |
| 5 | `triumvirate/meta_cognitive_planner.py` | `data/triumvirate/plan_state.json` | planner's current plan state |
| 6 | `api/tenants.py` | `data/tenants/{tenant_id}.json` (`MSB_TENANT_DIR`) | tenant records (already has a double-prefix fix + operator-gated writes) |
| 7 | `business/registry.py` | `data/truth/{entity_id}.json` (`MSB_TRUTH_DIR`) | the "Registry of Truth" — operator-gated, content-addressed |
| 8 | `api/graph.py` | `data/memory_graph/{session}.json` (`MSB_GRAPH_DIR`) | knowledge-graph session store |
| 9 | `agent/execution_loop.py` | `runtime/research/{slug}/STATUS.json`, `index.json` | Ralph loop state + integrity locks (triple-guarded: mtime/lock/schema) |
| 10 | `conversation/producer.py`, `task_producer.py` | `replay_cursor.json` | replay cursor (the `conversation.jsonl`/`task_events.jsonl` ledgers themselves are AUDIT, below) |

### EVIDENCE — per-run artifacts (keep file-based)

| Module | Files |
|---|---|
| `api/research.py`, `harnesses/research_assistant.py`, `flywheel/chargers.py` | `runtime/research/{slug}_{completion,evidence_ledger,state,UIM}.json` |
| `flywheel/engine.py` | `runtime/flywheel/{scans,uims}/{turn_id}.json`, `**/*_UIM.json` |
| `triumvirate/meta_cognitive_planner.py` | `data/triumvirate/{slug}/PLAN.json`, `stages/0{1..5}-*.json` |
| `conversation/producer.py`, `task_producer.py` | `claims.json`, `conversation.jsonl`, `task_events.jsonl` (append-only ledgers) |
| `experiments/`, `artifacts/hygiene/*`, `artifacts/qdrant-sweep-*` | governance/chaos run outputs (gitignored) |

### AUDIT — tamper-evident (keep file-based, 0600)

| Module | Files | Why it must stay a file |
|---|---|---|
| `uac/audit_chain.py`, `uac/chain_anchor.py` | `data/uac/chain_anchor.json` | the **external chain-tip anchor** — its whole purpose is out-of-band evidence *outside* the DB it protects; moving it into a DB would defeat it |
| `ops/backup.py` | `chain-anchor-notary.jsonl`, `manifest.json` | backup/notary records + manifest |

### CONFIG / external user data (keep file-based)

| Module | Files |
|---|---|
| `core/config.py` | NotebookLM `active-index.json` (external user data, `~/.notebooklm-library-deep-dive`) |
| `factory/test_runner.py` | read-only `package.json` detection (external repo language probe) |
| `api/dashboard.py` | `artifacts/hygiene/hygiene_aggregate.json` (read-only aggregate) |

### EPHEMERAL — client-local credentials (keep file-based, 0600)

| Module | Files |
|---|---|
| `device/client.py` | `device.json`, `session.json` (device identity + signed session, local client state) |

## Out of scope (gitignored, not "state stores")

Thousands of files under `artifacts/` (hygiene/qdrant-sweep outputs),
`runtime/` (research/flywheel run artifacts), `storage/` (Qdrant internal
segment/config JSON), `experiments/runs/`, `.claude/worktrees/` (a historical
worktree snapshot), and `make-scenarios/*.json` (Make.com workflow definitions).
These are CACHE/EVIDENCE, already gitignored, and not application state.

## Migration plan (the "replace" half of 1.3 — separate increments)

The common persistence architecture already exists: `db/sqlite.py` (raw
helper), `runtime/store.py` ("chain is the record, store is the projection"),
and `tasks/lifecycle.py` (durable task/event store). The STATE rows migrate
there in this order, each as its own tested commit:

1. **Tenants** + **truth registry** + **knowledge graph** (rows 6–8) — the
   three operator-gated registries; lowest risk, highest tenant-isolation
   payoff (Phase 10 dependency).
2. **Triumvirate state** (rows 1–5) — mission anchor, sbom, poison-pill,
   mesh, plan state; collapse `poison_pill.json` into the existing SQLite
   `governance/killswitch.py`.
3. **Ralph loop** (row 9) — STATUS.json + index.json → SQLite (already has
   integrity-lock semantics to preserve).
4. **Replay cursor** (row 10) — fold into the task/event store.

Do **not** migrate the AUDIT/EVIDENCE rows (chain anchor, ledgers, run
artifacts) — file-based is correct for them; the chain anchor especially is
an out-of-band trust boundary by design.

## Verification

`make lint` clean and full suite green are the preconditions for each
migration increment; the data migration itself must be a read-then-write
with the file left as a fallback until the SQLite path is verified, never a
destructive rewrite.
