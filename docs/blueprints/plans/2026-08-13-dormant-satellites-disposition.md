# Dormant Satellites — Disposition Plan

**Scope:** `src/sovereign_runtime/` and `src/personal_intelligence/` — the two
packages flagged by the 2026-08-08 deep-pass audit as "real, tested, but zero
cross-imports into `msb_v3`." This plan answers the follow-up question that
audit didn't ask: **should they be connected, or retired?**

**Execution status (2026-08-13):** Tasks 1–3 and the no-need branch of Task 4
are complete. The pytest gate now includes `personal_intelligence/tests`; the
six dead capability implementations and their dedicated planner/brain tests
are archived under `docs/audits/archived-satellites/2026-08-13/`; `event_bus`
and `SkillEngine` remain deferred and unadopted; no entity/relationship graph
replacement was built.

**Grounded, not aspirational:** every row below was read from the actual
source file (all files are 18–72 lines — small enough to read in full, not
sampled) on 2026-08-13, not carried over from the audit's summary.

> **Answer up front: mostly retire, don't connect.** Six of nine modules
> are strictly-weaker, non-persistent duplicates of code `msb_v3` already
> ships in production form. Wiring them in would violate this stack's own
> Complexity Governor rule (`Personal-AI-MoIE-Task-Scoped-Cognitive-Runtime.md`,
> "Can existing infrastructure solve it? ONLY THEN → new subsystem").

---

## 1. The honest inventory

| Dormant module | What it actually is | Live equivalent already shipped | Verdict |
|---|---|---|---|
| `sovereign_runtime/brain/ail_pipeline.py` | `AILPipeline.run()` — self-labeled `"""...placeholder."""`, returns hardcoded empty `assumptions/inversions/predictions` lists. No LLM call, no logic. | `triumvirate/meta_cognitive_planner.py` `MetaCognitivePlanner` — real 5-stage plan decomposition, writes artifacts, SQLite-backed. | **Archive.** Not a real implementation of anything; nothing to lose. |
| `sovereign_runtime/brain/moie_swarm.py` | `MoIESwarm.debate()` — self-labeled `"""...placeholder."""`, returns a hardcoded fixed winner and `rounds: 0`. No agents actually run. | Same as above — `meta_cognitive_planner.py` is the real MoIE-plane implementation. The flywheel's own plan doc (`2026-08-11-phase2-flywheel.md`) already independently flagged this exact file as a stub when scoping Phase 2. | **Archive.** Confirmed stub by two independent readings, 5 days apart. |
| `sovereign_runtime/brain/recursive_planner.py` | `RecursivePlanner.analyze()` — "complex" detection is `len(goal) < 80` chars; splitting is `goal[:midpoint], goal[midpoint:]` (literal string bisection, not semantic decomposition). In-memory `PlannerMemory` (dict, no persistence). | `agent/planner.py` + `agent/dag.py` + `agent/executor.py` — LLM-first task DAG with template fallback, topological execution, retry policy, timeout, verification registry. Categorically more capable. | **Archive.** |
| `sovereign_runtime/events/event_bus.py` | Real, functional, thread-safe in-process pub/sub. The one piece here that isn't a stub. `BrainService` subscribes it to `agent.goal.received` → emits `agent.plan.created` / `agent.execute.request` — but nothing in the live server ever emits or subscribes to those event names, so the wiring is a closed loop talking to itself. | No direct equivalent — `msb_v3` uses direct calls + `uac/audit_chain.py` for the observability an event bus would give you. | **Don't adopt yet.** Genuinely reusable *if* a real decoupling need shows up (e.g., flywheel stages wanting to fan out to multiple listeners). Revisit only when that need is concrete — see Task 3. |
| `personal_intelligence/context_engine` | `ContextEngine` — in-memory list + dict, `search()` is `if lower in c.content.lower()` substring matching. No persistence, no embeddings. | `retrieval/engine.py` (`RetrievalRouter`) + `retrieval/planner.py` — real RRF fusion across multiple indexes, Qdrant-backed, provenance-annotated, graceful per-route degradation. | **Archive.** |
| `personal_intelligence/memory_graph` | `MemoryGraph` — in-memory `Dict[str, Entity]` + list of relations. No persistence — restarts lose everything. | `memory/store.py` (SQLite `MemoryStore`) covers session/message memory; nothing live does entity/relationship graphing specifically — this is the one gap without a direct replacement (see §2). | **Archive the code, note the gap** (§2) — don't resurrect this implementation to fill it; it has no persistence, which is disqualifying on its own. |
| `personal_intelligence/provenance` | `MemoryLedger` — in-memory `Dict[str, ProvenanceEntry]`, no persistence. | `uac/audit_chain.py` — genuine hash-chained, tamper-evident, SQLite-backed audit trail. Its own docstring records that it was built 2026-08-02 specifically because a chained provenance store was "confirmed absent from the codebase by direct search" at the time — i.e., this exact gap was already found and closed once, five months before this dormant module was even in scope. | **Archive.** Superseded before this conversation started. |
| `personal_intelligence/skill_engine` | `SkillEngine.load_directory()` reads real `**/SKILL.md` files (frontmatter + body), `match()` does trigger-keyword scoring. | `api/skill_router.py` discovers/executes skills from `~/.hermes/skills` live, in production. Different directory convention, and skill_router has no keyword-matching (`match()`) — it expects the caller to already know the skill name. | **Narrow-port candidate, not archive** — see Task 4. The one piece of unique, non-duplicated logic in either package. |
| `personal_intelligence/agent_factory` | `AgentFactory.build_blueprint()` — glues the three archived pieces above together. No persistence, depends entirely on modules being archived. | N/A — purpose is fully superseded once its three dependencies are. | **Archive.** |

**Net:** of 9 modules, **6 are dead code that should be formally retired**, 1
(`event_bus`) is real but has no live consumer, 1 (`memory_graph`'s *role*,
not its code) marks a genuine gap, and 1 (`skill_engine`'s `match()` logic
specifically) may be worth a narrow port.

---

## 2. A concrete bug this investigation found

`pyproject.toml` → `testpaths = ["tests", "src/sovereign_runtime/tests"]`.

`src/sovereign_runtime/tests` **is** in the default suite (56 tests run on
every `pytest`/`make test`/CI invocation — more than the 08-08 audit's count
of 21, so it's grown since). `src/personal_intelligence/tests` **is not** —
its 15 tests only run if you point pytest at that path explicitly, which
nothing in `make test`, CI, or `verify-release.sh` does. They still pass
today (checked directly), so nothing has silently rotted *yet* — but they
are not exercised by the 814-passed release-verification number in
`CHANGELOG.md`, contrary to what that number implies.

This is exactly the kind of drift the portability/seeding work elsewhere in
this repo exists to prevent. Fix it one of two ways, not both:

- **If archiving (recommended, see §1):** archive
  `src/personal_intelligence/{context_engine,memory_graph,provenance,agent_factory}`
  and `src/sovereign_runtime/brain/{ail_pipeline,moie_swarm,recursive_planner,
  planner_memory,plan_models}.py` plus the `BrainService` glue. Keep
  `personal_intelligence/skill_engine` as the deferred narrow-port source,
  and keep `event_bus.py` + `core/` + `config/` (still real, still tested).
  Update `sovereign_runtime/__init__.py`'s exports accordingly.
- **If keeping anything from `personal_intelligence` (e.g. for Task 2's
  narrow port source), add `"src/personal_intelligence/tests"` to
  `testpaths` immediately** so it's never again silently excluded from the
  gate — do this regardless of which way §1's archive decision goes, as
  long as the directory exists at all.

---

## 3. Recommended tasks, in order

### Task 1 — Fix the testpaths gap (§2)
Either wire `personal_intelligence/tests` into `testpaths` or remove the
directory. Either resolution is a few minutes of work; leaving it
half-in/half-out is the only wrong answer.

### Task 2 — Archive decision, written down where the next reader will find it
Don't just delete silently. For each archived module, either:
- move it under `docs/audits/archived-satellites/` with a one-line note
  pointing to its live replacement (mirrors how `uac/audit_chain.py`'s own
  docstring already documents *why* it exists relative to
  `triumvirate/argus_auditor.py` — this repo has a real convention for
  this), or
- delete it and record the mapping table from §1 in `CHANGELOG.md` under
  `[Unreleased]`, so `git log`/`git blame` carries the reasoning.

This closes the exact hole that caused this investigation: a prior audit
(08-08) found these modules, correctly reported them as tested-but-unwired,
and that finding sat for 5 days as an open question with no answer recorded
anywhere — which is how "should we connect this?" got asked again from
scratch today.

### Task 3 — `event_bus` adoption: defer, don't build speculatively
Do not wire `EventBus` into `agent/` or `flywheel/` now. Revisit only when a
specific stage genuinely needs to fan out to more than one listener without
the caller knowing who's listening — e.g. if the Cockpit's live panels want
to subscribe to flywheel stage transitions without `flywheel/` importing
`api/cockpit.py`. Until that concrete need exists, direct calls +
`uac/audit_chain.py` cover the same observability need with less moving
parts, matching this repo's own YAGNI non-goal in
`2026-08-11-adaptive-build-environment.md`.

### Task 4 — `skill_engine.match()` narrow port (only if needed)
Before porting: check whether anything calling `api/skill_router.py`
(`agent/planner.py`'s capability-to-tool mapping, `agent/intent.py`) already
needs fuzzy/trigger-based skill selection rather than exact-name lookup. If
yes, port just the trigger-scoring algorithm (`SkillEngine.match()`, ~10
lines) into `skill_router.py` against its real `~/.hermes/skills` /
`SKILL.md` convention — don't import the whole `personal_intelligence`
package for one method. If no caller needs it, this task doesn't exist;
don't build it speculatively.

### Task 5 — the one real gap: entity/relationship memory (optional, low priority)
`memory_graph`'s *role* (durable entities + relationships, not its
non-persistent code) has no live equivalent. Nothing currently asks for
this — Qdrant + `memory/store.py` cover retrieval and session memory. Don't
build a SQLite-backed replacement speculatively; note it here so if a real
use case shows up (e.g. Cockpit wanting to visualize entity relationships
across research runs), the gap is already documented instead of
rediscovered.

---

## 4. Global constraint

Same rule as every other plan in this directory: **the engine never bypasses
the brakes.** None of Tasks 1–5 touch `governance/` — this is dead-code
cleanup and one narrow, need-gated port, not new autonomous capability. No
approval-queue item is required for Task 1/2 (housekeeping); Task 4, if it
ever executes, adds a code path inside the existing skill-execution surface,
which is already governed the same way `/skills/execute` is today.

## 5. Execution record

The selected disposition was executed without adopting speculative systems:

- `pytest` now collects `src/personal_intelligence/tests`.
- Archived: the six dead capability implementations (`AILPipeline`,
  `MoIESwarm`, `RecursivePlanner`/`PlannerMemory`, `ContextEngine`,
  `MemoryGraph`, `MemoryLedger`, and the dependent `AgentFactory` glue),
  plus `BrainService`/planner support and their dedicated tests.
- Retained but deferred: `event_bus` and `SkillEngine`.
- Explicitly not built: durable entity/relationship memory and trigger-based
  skill selection in the live router.
