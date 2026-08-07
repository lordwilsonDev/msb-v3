# Technical Debt Report — SMI-017-v1.0

## Duplication

1. **Two independent, unconnected vector stores.** `api/rag.py` is a real
   tenant-scoped Qdrant client with Ollama embeddings (external ANN index,
   768-dim, cosine). `triumvirate/hardware_sovereignty.py:VectorHippocampus`
   is a from-scratch SQLite reimplementation storing embeddings as a JSON
   blob column and computing cosine similarity in a pure-Python loop over up
   to `limit * 10` rows per query (`hardware_sovereignty.py:126-144`). They
   share no code, no schema, no embedding call. One of these should not
   exist; if the SQLite one is meant as an offline/no-Qdrant fallback, it
   needs to say so and be routed through the same interface as `rag.py`.

2. **Copy-pasted hashing helper.** `_goal_signature()` — identical
   implementation — appears in both `triumvirate/mission_anchor.py:53-55`
   and `triumvirate/meta_cognitive_planner.py:54-56`. Extract to a shared
   `triumvirate/_hashing.py` or similar.

3. **Five bespoke single-file JSON persistence patterns** doing the same
   thing (`_load()`/`_save()` around `Path.write_text(json.dumps(...))`,
   no locking) with independent implementations: `mission_anchor.py`,
   `hardware_sovereignty.py` (mesh state), `meta_cognitive_planner.py` (plan
   state), `guardian_scanner.py` (SBOM + poison pill — 2 files). None share
   a helper module despite doing byte-for-byte the same read/write/mkdir
   dance.

## Dead / orphaned code

4. **`uac/` package (7 files, ~900 lines) is unreachable from the running
   service.** No router mounts it; the only callers are its own tests and
   internal cross-imports within `uac/`. It's real, tested, well-documented
   code that currently can't be exercised outside `pytest`.

5. **`api/smi.py` is 76 lines of hard-coded stub responses** presented as
   the flagship SMI semantic API (`/smi/query|evaluate|adapt|report`) — see
   `current_architecture.md` §4 and `production_risks.md` #7 for the
   consequences of shipping this as if it were real.

## Fake abstractions / "looks distributed, isn't"

6. **`ClusterAwareDiscovery` is bookkeeping, not clustering.** `register_peer`
   writes a peer's host/port/capacity/`last_seen` into a JSON file once;
   `last_seen` is never refreshed after registration, there's no heartbeat,
   no health probe, no expiry, and nothing in the codebase ever actually
   opens a connection to a registered peer. "Hardware Sovereignty" cluster
   support is a data model with no networking behind it.

7. **Multimodal interfaces are stubs by design** (`triumvirate/multimodal_interfaces.py`,
   57 lines) — vision "capture," haptic "heartbeat," and speech "command
   mapping" — fine as placeholders, but they're mounted as real endpoints
   (`/triumvirate/multimodal/*`) with no indication in the API surface that
   they don't do anything yet.

## Packaging / dependency hygiene

8. **Undeclared runtime dependency**: `qdrant_client` is imported in
   `api/rag.py` but absent from `pyproject.toml` and `requirements.lock`.
   See `production_risks.md` #9.

9. **Declared but unused dependency**: `pydantic-settings` is listed in
   `pyproject.toml` but `core/config.py` explicitly avoids it in favor of a
   plain dataclass + `os.getenv`. See `production_risks.md` #13.

10. **No `[build-system]` table in `pyproject.toml`** — the package cannot
    reliably be `pip install -e .`'d, which is the root cause of the test
    collection fragility in `production_risks.md` #8.

## Oversized files / mixed concerns

11. **`agent/ralph_loop.py` (685 lines, ~10% of `src/` by line count) does
    at least nine distinct jobs in one file**: dataclass state schema,
    atomic file I/O with fsync/backup, content hashing, mission-integrity
    binding, per-iteration evaluation scoring, self-improvement/patch
    proposal, scope evolution with audit trail, file-lock concurrency
    control, self-annealing diagnosis, the main loop driver, a
    research-action factory, and a separate `LoopMemory` class. This is the
    single largest complexity hotspot in the repo and the hardest file to
    safely change — splitting state/IO, evaluation, and the loop driver
    into separate modules would make each testable and reviewable in
    isolation.

12. **`api/research.py` (404 lines)** and **`triumvirate/guardian_scanner.py` /
    `uac/stage_0_knowledge_acquisition.py` (~290 lines each)** are the next
    tier of size; none are unmanageable individually, but `research.py`
    mixes route handling with what looks like harness-orchestration logic
    that would be clearer split the way `harnesses/` already separates
    concerns elsewhere.

## Process / git hygiene

13. **Oversized, mislabeled commits.** `23c074f` — titled
    `"feat: add multi-tenant isolation layer"` — actually introduces the
    entire `uac/` package (a separate, substantial feature) plus unrelated
    research-runtime artifact files and a stray `mcp_adapter.py` /
    `hyperframes`. Anyone auditing "when was UAC added" from commit
    messages alone would miss it entirely. See `current_architecture.md`
    §3 step 7.

14. **Committed runtime state.** `data/` is `.gitignore`d (line 4) but
    `data/msb_v3.db`, `data/memory_graph/*.json`, and all of
    `data/triumvirate/*` are tracked anyway — meaning they were force-added
    at some point after the ignore rule existed, and every local run now
    dirties tracked files. See `production_risks.md` #10 for the concrete
    consequence (a stale committed `poison_pill.json` that falsifies the
    shipped regression report).

## Hard-coded environment assumptions

15. **`api/mcp_bridge.py` hard-codes `/Users/lordwilson/Documents/Vault`**
    across 11 separate call sites (`vault_list/read/write/append/patch/delete/move/get_document_map/search_query/search_simple/tag_list`,
    `mcp_bridge.py:67,75,81,87,95,112,119-120,128,149,163,173`) instead of
    reading it from config. This directly contradicts the project's own
    "sovereign, portable, local-first" framing — the code will not run
    correctly for any other user or deployment target without source edits.

## Priority ordering

If only three items get fixed before further feature work: (a) delete or
gate `api/smi.py`'s stub endpoints so they can't be mistaken for real
behavior, (b) untrack `data/` and regenerate a clean release snapshot, (c)
make the package pip-installable to kill the test-collection fragility.
Everything else here is real but lower urgency than those three, which each
actively mislead someone (a caller of `/smi/*`, a reader of the regression
report, a CI system) right now.
