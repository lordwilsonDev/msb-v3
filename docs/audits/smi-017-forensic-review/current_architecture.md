# msb-v3 Current Architecture — SMI-017-v1.0 Forensic Map

Reviewed at tag `SMI-017-v1.0` (commit `d9d8466`), detached HEAD, worktree
`/Users/lordwilson/msb-v3/.claude/worktrees/agent-a9ce292a7063519a8`.

## 1. Module inventory (real, verified by reading every file)

| Module | Files | Lines | Role |
|---|---|---|---|
| `api/` | 24 | ~2,700 | FastAPI routers, one file per concern, all mounted in `api/app.py` |
| `triumvirate/` | 6 | ~1,100 | "Triumvirate OS" — planner, mission state, guardian security, self-audit, cluster/vector stubs, multimodal stubs |
| `uac/` | 7 | ~900 | Universal Agent Creator pipeline — requirements → knowledge manifest, hash-chained audit trail, artifact library |
| `agent/` | 1 | 685 | `ralph_loop.py` — deterministic single-agent state-machine harness |
| `local_ai/` | 3 | ~340 | Ollama + llama.cpp clients, backend-switching factory |
| `harnesses/` | 2 | ~330 | `BaseHarness`/`ChatHarness` abstraction, research-assistant harness |
| `memory/`, `db/`, `core/`, `guardrails/`, `business/`, `observability/` | 1 each | ~410 total | SQLite message history, raw SQLite connection helper, env-var config, tool-loop step enforcement, "Registry of Truth", Prometheus metrics |

Total: 48 `.py` files, 6,600 lines under `src/msb_v3/`.

There is no `security/`, `evaluation/`, `adaptation/`, `continuous/`, `retrieval/`,
`routing/`, or `providers/` module. Anything an earlier draft of this brief
mapped to those names does not exist in this codebase.

## 2. Data flow

```
HTTP client
  -> api/app.py (FastAPI factory: CORS(*), gzip, request-id, one hard-coded
     rate limiter for /research/assistant/run only)
     -> 22 routers mounted directly, no shared auth dependency
        -> api/chat.py -> harnesses/base.py:ChatHarness
             -> local_ai/client_factory.py:get_client() -> ollama.py or llama_client.py
             -> memory/store.py:MemoryStore (SQLite, per-call sqlite3.connect)
        -> api/triumvirate.py -> triumvirate/*.py (module-level singletons:
             planner, anchor, guardian, sbom, poison_pill, argus,
             cluster_discovery, hippocampus — instantiated once at import time
             and shared by every request/thread)
        -> api/rag.py -> Qdrant (external, real ANN vector DB) + Ollama embeddings
        -> api/smi.py -> pure in-memory stub logic, no backend at all
        -> api/mcp_bridge.py -> either proxies back into this same app over
             HTTP, or reads/writes files directly under a hard-coded
             `/Users/lordwilson/Documents/Vault` path
        -> business/registry.py, api/tenants.py -> flat JSON files under data/
```

`uac/` is not reachable from any router. It is pure library code, imported
only by its own tests and by `stage_0_knowledge_acquisition.py` internally.

## 3. SMI-010 → SMI-017 lifecycle, reconstructed from git log

Only one SMI tag exists (`SMI-017-v1.0`); there is no per-phase artifact
trail. The 81-commit history (`3046054` scaffold → `d9d8466` release) shows
the real build order:

1. `3046054`/`0e29776` — FastAPI + Ollama + SQLite scaffold, no auth, no memory.
2. `95c6d27` — SQLite-backed chat memory.
3. `a7f4024` — `guardrails/fold.py` step-enforcement (ported from an external
   "forge" project per its own docstring).
4. `79a2a9d` → `a8a9955` → `7d7c91e` — **Triumvirate Phases 1–6** land in three
   fast commits: planner, mission anchor, guardian scanner, argus auditor,
   cluster/hippocampus, multimodal stubs — the whole `triumvirate/` package
   arrives essentially at once, not incrementally per phase as the docs imply.
5. `637ebcf`/`65cf535` — home dashboard wired to Triumvirate status.
6. `e369544`/`b927b1f` — Prometheus metrics for Triumvirate.
7. `23c074f` **"feat: add multi-tenant isolation layer"** — this single commit's
   message describes only `api/tenants.py` + `api/tenant_chat.py`, but its
   diff also introduces the entire `uac/` package (7 files) plus unrelated
   research-runtime JSON/markdown artifacts and a stray top-level
   `hyperframes` file and `mcp_adapter.py`. The commit message does not
   describe most of what it actually shipped — there is no clean, honest
   commit boundary for "when UAC was introduced."
8. `797b49a` → `d4e7f35` — Qdrant RAG integration (the actual last feature
   work before the tag): scaffold → ingestion pipeline → real Ollama
   embeddings wired in.
9. `d9d8466` **"chore(smi-017): release artifacts"** — writes
   `artifacts/SMI-017/*.json` and tags the release. `api/smi.py`'s
   `/smi/query|evaluate|adapt|report` endpoints appear to be new in or just
   before this commit and are **not connected to the Qdrant/RAG work landed
   one commit earlier** — see finding below.

## 4. Are the pieces actually wired together?

**No, only partially, and in one direction.**

- `uac/stage_0_knowledge_acquisition.py` imports
  `triumvirate.mission_anchor.MissionAnchor` and uses it for scope-locking —
  this is the *only* cross-module dependency between `triumvirate/` and
  `uac/`. It runs one way: UAC consumes Triumvirate. Triumvirate never reads
  anything UAC produces (no import of `uac.*` anywhere under `triumvirate/`
  or `api/`).
- `uac/` has **zero HTTP surface**. No `api/uac.py`, no router, not mounted
  in `api/app.py`. The most substantial, best-documented pipeline in the
  repo (Stage 0: validate → research → normalize → confidence-score →
  manifest → hash-chain audit → publish to `AxiomLibrary`) cannot be invoked
  by anything outside a Python test.
- `api/smi.py` — the endpoints the whole checkpoint is named after
  (`/smi/query`, `/smi/evaluate`, `/smi/adapt`, `/smi/report`) — are **fully
  hard-coded stubs**. `/smi/query` returns
  `[{"score": 0.92, "source": f"{query}-seed-1"}, {"score": 0.87, ...}]`
  regardless of input; `/evaluate` computes a fixed `0.75` baseline nudged by
  a formula over the request body; `/adapt` echoes `{source: target}`;
  `/report` returns a fabricated file path string. None of them call
  Qdrant, Ollama, or any module under `triumvirate/`/`uac/`, despite real
  Qdrant-backed retrieval (`api/rag.py`) existing in the same codebase one
  commit earlier. (`src/msb_v3/api/smi.py:35-76`)
- `artifacts/SMI-017/*.json` are not produced by any pipeline in this repo —
  there is no script, CI job, or Makefile target that generates them (`grep`
  for their filenames returns nothing outside `artifacts/`). They read as
  hand-authored release notes, not machine output — confirmed independently
  in `production_risks.md` (regression/security artifacts contradicted by
  live re-run).

## 5. Duplication and structural debt worth naming here

- **Two independent vector stores**: `api/rag.py` (real Qdrant + Ollama
  embeddings, tenant-scoped) and `triumvirate/hardware_sovereignty.py:VectorHippocampus`
  (SQLite blob column + Python-loop cosine similarity, O(n) scan). They do
  not share a client, an embedding call, or a schema, and nothing routes
  between them.
- **Duplicate hashing helper**: `_goal_signature()` is defined identically
  in both `triumvirate/mission_anchor.py:53-55` and
  `triumvirate/meta_cognitive_planner.py:54-56` — copy-pasted, not shared.
- **Global module-level singletons instead of dependency injection**:
  `api/triumvirate.py:26-33` instantiates `planner`, `anchor`, `guardian`,
  `sbom`, `poison_pill`, `argus`, `cluster_discovery`, `hippocampus` once at
  import time and every request shares them — there is no per-request or
  per-agent scoping anywhere in `triumvirate/`.
- **Filesystem as database**: `mesh_state.json`, `plan_state.json`,
  `STATUS.json`, `poison_pill.json`, `sbom_registry.json` are each a single
  JSON file rewritten wholesale (`Path.write_text`) on every update, with no
  shared read/write abstraction — five bespoke ad hoc persistence
  implementations doing the same thing slightly differently.

See `scale_failure_analysis.md` for why this specific pattern is the
project's biggest scaling risk, and `sovereign_agent_factory_phase2.md` for
what `triumvirate/`/`uac/` would need to become a real multi-agent factory.
