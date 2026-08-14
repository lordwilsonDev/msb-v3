# msb-v3 Glossary

A decoder for the named subsystems in this repo. Constraints the names
impose, where they live in the code, and what they actually do.

**Reading order**: if you are new to the codebase, read **Triumvirate** →
**External Integrations** → **Other Subsystems** in that order. The rest
is fine to skim.

> Naming note: the mythology (Triumvirate, Argus, Hippocampus, etc.)
> predates this repo and is a deliberate architectural pattern, not
> ceremony. Class/file names that look like "subsystem names" are part
> of the API contract (metric labels, ledger event names, config keys)
> — don't rename them casually. Renames that have already landed as
> path-only (file moved, class name kept) are marked **path-trimmed**.

---

## Triumvirate — the 3-component architecture

The pattern: every action passes through three guardrails: a
**scanner** that decides whether the action is permissible, an
**auditor** that records what happened, and a **memory** that
remembers the result. No actor in the system has direct write access
to durable state — every write is wrapped in this pattern.

Files: `docs/triumvirate.md` (full design doc),
`src/msb_v3/triumvirate/`.

### · Guardian — the scanner
**Decision layer.** Statically scans scripts before they run. Rejects
calls into dangerous paths (writes outside the repo, exec of untrusted
binaries, network calls without allow-list) and tracks the SBOM.

- `GuardianScanner` — `src/msb_v3/triumvirate/guardian_scanner.py:166`
- `SBOMRegistry` — `src/msb_v3/triumvirate/guardian_scanner.py:197`
- `PoisonPill` — killswitch primitive that pauses every mission
  atomically (`guardian_scanner.py:215`)

### · Argus — the auditor
**Recording layer.** Records every significant runtime event into a
tamper-evident **audit chain** (hash-linked Merkle log) and into a
**mulch learnings** DB so the next planner can read what worked and
what didn't.

- `ArgusAuditor` — *path-trimmed*, now at
  `src/msb_v3/observability/audit.py:51` (folded into observability —
  auditing *is* observability)
- `_MULCH_DB` — SQLite store of "what we tried" notes, surfaced from
  the dashboard under "Recent learnings"
- Tamper detection lives at
  `src/msb_v3/uac/audit_chain.py` (sovereign-audit-chain verification;
  orthogonal to Argus's pattern-scanning role)

### · Hippocampus — the memory
**Recall layer.** Embeddings + SQLite for semantic search over the
agent's own decisions and the user's prior context. Uses Qdrant (via
`src/msb_v3/retrieval/indexes.py`) when reachable; falls back to local
numpy cosine when Qdrant is offline.

- `VectorHippocampus` — `src/msb_v3/triumvirate/hardware_sovereignty.py:79`
- `ClusterAwareDiscovery` — same file: peer-node mesh so a fleet of
  sovereign boxes can share Hippocampus state

### Triumvirate adjuncts (not part of the 3, but live in the same package)

| Name | What | File |
|---|---|---|
| **Mission Anchor** | The status engine (`STATUS.json`) that every mission reads to know its current state and writes to commit transitions | `src/msb_v3/triumvirate/mission_anchor.py` |
| **Meta-Cognitive Planner** | Goal → DAG-compiler. Plans, retries, escalates | `src/msb_v3/triumvirate/meta_cognitive_planner.py` |
| **Hardware Sovereignty** | Cluster discovery + `VectorHippocampus`; Mesh | `src/msb_v3/triumvirate/hardware_sovereignty.py` |
| **Multimodal Interfaces** | `VisionClaw` (screen capture), `HapticHeartbeat` (CoreHaptics pulse), `SpeechFunctions` (intent mapping). Currently `status="stub"`; gated behind `MSB_MULTIMODAL_ENABLED=1` — see `src/msb_v3/api/triumvirate.py` | `src/msb_v3/triumvirate/multimodal_interfaces.py` |

API surfaces:
- `/triumvirate/plan` — Meta-Cognitive Planner
- `/triumvirate/multimodal/{vision,haptic,speech}/...` — Multimodal Interfaces
- `/triumvirate/hippocampus/{upsert,search}` — Hippocampus
- `/cockpit/api` — Cockpit (was: `/cockpit`; see dashboard below)

---

## External integrations

These are **not** internal subsystems — they are boundary names
referring to systems outside this repo.

### · Hermes — external agent runtime
**An adjacent agent system, not an internal module.** `~/.hermes/` is
the user-data root for the external Hermes agent runtime; we
consume its skills and emit ledger events to its verification
endpoint. MSB_SKILLS_DIR overrides the default path; the runtime
contract is governed by the Hermes project, not ours.

- `src/msb_v3/api/skill_router.py` — the only entry point
- `~/.hermes/skills/` — skill source
- `~/.hermes/config.yaml` — Hermes-side config MSB reads

The skill-router endpoint path (`/skills/{name}/run`) is *our*
surface; Hermes itself doesn't know about MSB.

### · Vault — Obsidian user-data directory
Wilson's local Obsidian vault at `~/Documents/Vault` (override:
`MSB_VAULT_PATH`). The semantic-search subsystem reads from it via
Qdrant. **User data, not repo data** — never tracked by git.

- Index adapter: `src/msb_v3/retrieval/indexes.py`
- Default tenant: `wilson-vault` (search-side constant)

### · Qdrant — vector store
The semantic-search backend. Per-tenant collections. Lives outside
the app process; MSB talks to it over HTTP. Falls back to local
numpy cosine when unreachable (graceful degrade — never "hangs").

- Wrapper: `src/msb_v3/api/rag.py:_qdrant_client`
- Config: `QDRANT_HOST` (default: `localhost`) + `QDRANT_PORT`

### · NotebookLM
Google NotebookLM integration for research runs (multimodal source
synthesis + active-cluster gating). Live at `~/.notebooklm-library-deep-dive/active-index.json`.

- The "Oracle Protocol" (NotebookLM artifact stream) routes through
  `src/msb_v3/api/research.py`
- 136-source index lives outside the repo, user-data

### · Ollama / llama.cpp — local LLM backends
- Ollama (`OLLAMA_URL`, default `qwen3:8b`) — everyday chat and skill
  routing
- llama.cpp (`LLAMA_CPP_URL`) — heavier lifting, GGUF model files in
  `~/models/`
- Frontier seam (`OPENAI_FRONTIER_URL/MODEL`) — the hybrid router's
  Phase-2 upgrade path; closed (router degrades to local) until
  `OPENAI_API_KEY` is set

---

## Other major subsystems

### · Vesta — task + evidence + transport
The "remote execution" subsystem. Pulls tasks from a queue, runs
them in a sandboxed subprocess, and **publishes evidence** (hashes of
inputs/outputs/exit codes) back as a tamper-evident log.

- Surface: `/vesta/transport`, `/vesta/evidence`, `/vesta/shell`,
  `/vesta/write`, `/vesta/read`
- Tunable: `MSB_VESTA_REQUIRE_TUNNEL` (fail-closed when WireGuard
  not present)
- File split: `vesta/{api,adapter,approvals,dev_harness,evidence,
  models,policy,read,runtime,shell,transport,write}.py`

### · Ralph — execution loop *(path-trimmed)*
The deterministic agent state machine behind `/assistant/ralph-loop`.
Triple guards: status-mtime integrity, lock-file, versioned schema.

- File: *path-trimmed* to
  `src/msb_v3/agent/execution_loop.py` (class name kept as
  `RalphLoopHarness` for ledger/back-compat reasons — see
  `docs/glossary.md` if renaming wires is on your roadmap)
- API: `POST /assistant/ralph-loop` (kept stable; ledger events
  emit `event=ralph_loop:{completed,exhausted,escalated,…}`)

### · Cockpit — read-only dashboard *(path-trimmed)*
The single read-only HTML surface that aggregates every panel:
services, mission, guards, flywheel, audit chain, vault freshness,
research runs, memory, rate-limit rejections.

- File: *path-trimmed* to `src/msb_v3/api/dashboard.py`
- Mount point: `/cockpit/api` (path kept — see file's docstring for
  why)
- Probe budget: every panel is error-contained; one dead service =
  one dead panel, never the whole page

### · Mulch
The cross-runtime "what did we learn" buffer. Both Argus (audit
side) and Vesta (evidence side) write to it; the planner reads it
to seed retries with priors. Storage: `runtime/triumvirate/mulch_learnings.db`.

### · Sacred Lock (`PoisonPill`)
The killswitch primitive. Writes a single marker file (`Pause`) that
every long-running loop checks on its quiet tick. Used by Guardian
(`PoisonPill.pause_missions()`) and reachable from `/system/circuit-breaker`.

---

## Cross-cutting concepts

| Concept | File / Surface | Description |
|---|---|---|
| **Mulch findding** | `observability/audit.py:44` (`MulchFinding`) | A tagged note read by the next planner iteration |
| **STATUS.json** | `triumvirate/mission_anchor.py` | Single source of truth for "what state is this mission in?" |
| **SBOM** | `triumvirate/guardian_scanner.py` (`SBOMRegistry`) | Per-tool capability registry — who can call what |
| **Frontier seam** | `core/config.py:OPENAI_FRONTIER_*` | Phase-2 hybrid router's open/closed switch |
| **Vesta transport** | `vesta/transport.py` | Async task pull (queue) with WireGuard-gated admission |
| **Cockpit probe budget** | `api/dashboard.py:_PROBE_TIMEOUT_S` | The 4-second cap that makes the dashboard survive a dead service |

---

## If you are renaming something

Two safe categories:

1. **Class names that don't appear on the wire**. Renaming inside a
   private module is fine.
2. **Module paths that don't appear in any public API surface or
   metric label**. The path-trims above (Cockpit → dashboard, Argus
   → observability/audit, Ralph → agent/execution_loop) demonstrate
   this; the rules are:
   - Update all `from X import` call sites
   - Update pyproject.toml testpaths
   - Update CI / docker-compose healthcheck strings
   - Run `pytest`, `mypy`, `ruff check` before committing

Two unsafe categories:

1. **Anything that shows up in a metric label** — `TRIUMVIRATE_*`,
   `HIPPOCAMPUS_*`, the `event=ralph_loop:` ledger tokens. Rename
   these and you break dashboards and existing ledger history.
2. **Anything in the ledger/triamvirate/spec frozen docs** —
   `docs/triumvirate.md`, `docs/conversation-ledger-producer-v1.md`,
   `docs/conversation-envelope-v1.md`. Those are versioned contracts.

If you want to trim ceremony further, the remaining ceremony names
that survive because they're contractual are: **Triumvirate** (the
pattern + the metric labels), **Argus** (`ArgusAuditor` class name —
kept for ledger back-compat), **Hippocampus** (`VectorHippocampus`),
**Mission Anchor** (`STATUS.json` writer), **Mulch**, **Sacred
Lock/PoisonPill**. All others above are cosmetic and can go when
their surfaces do.
