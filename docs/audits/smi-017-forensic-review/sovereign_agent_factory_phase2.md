# Sovereign Agent Factory — Phase 2 Blueprint

Grounded in what `src/msb_v3/triumvirate/`, `src/msb_v3/uac/`, and
`src/msb_v3/agent/ralph_loop.py` actually contain today (all read in full),
compared against the design intent in `docs/triumvirate.md` and
`docs/triumvirate-plan.md`. This is not a rewrite plan — it names the
specific abstraction each existing module is missing before it can support
more than one agent at a time.

## What already exists and is real

| Component | What it actually does | File |
|---|---|---|
| `RalphLoopHarness` | Deterministic per-workdir state machine: atomic double-write + fsync STATUS.json, budget/scope/stagnation circuit breakers, self-annealing diagnosis→prescription, file-lock concurrency guard, evaluation scoring, memory of past runs (`LoopMemory`) | `agent/ralph_loop.py` (685 lines) |
| `MissionAnchor` | Scope-lock + circuit-breaker state machine backed by one JSON file | `triumvirate/mission_anchor.py` |
| `GuardianScanner` / `PoisonPill` / `SBOMRegistry` | AST-based dangerous-call scanner (with import-alias and `getattr` obfuscation resolution — genuinely well built), sha256-verified server trust registry, kill switch | `triumvirate/guardian_scanner.py` |
| `ArgusAuditor` | Pattern-scanning linter + "mulch" learnings store with resolve/unresolved tracking | `triumvirate/argus_auditor.py` |
| `ClusterAwareDiscovery` / `VectorHippocampus` | Peer registry, toy vector store | `triumvirate/hardware_sovereignty.py` |
| UAC Stage 0 pipeline | Requirements → 7-category research → evidence scoring → conflict flagging → versioned Knowledge Manifest → published to `AxiomLibrary`, every step logged to `ObserverLog` and `AuditChain` | `uac/stage_0_knowledge_acquisition.py` + `uac/audit_chain.py`, `uac/axiom_library.py`, `uac/observer_log.py`, `uac/models.py` |
| `AuditChain` | Genuine hash chain (each record hashes its content + prev hash), `verify_chain()` detects tampering | `uac/audit_chain.py` |

The UAC pipeline is, by a clear margin, the most "agent-factory-shaped" code
in the repo: it already produces auditable, versioned, source-attributed
artifacts through a fixed pipeline with a validation gate ("never guess
profession/jurisdiction"). It is also completely disconnected from the
running service — see `current_architecture.md` §4.

## What `docs/triumvirate.md` / `docs/triumvirate-plan.md` promised vs. delivered

The docs describe six phases (Meta-Cognitive Planner, [Phase 2 — absent from
the docs file itself], Guardian Protocol, Argus Auditor, Hardware
Sovereignty, Multimodal Interfaces) as OS-level "surfaces." All six have
matching endpoints in `api/triumvirate.py`. The gap is not "undelivered
endpoints" — it's that every phase was built as a **single global
instance**, matching a single-agent, single-operator mental model, not the
multi-agent one the "Triumvirate" and "Hardware Sovereignty / cluster mesh"
naming implies. `docs/triumvirate-plan.md`'s own next-steps list ("add
integration tests," "wire live status into home dashboard," "add
retry/backoff to Argus") is about hardening the single-instance system, not
about scaling it — Phase 2 in the sense this review means it was never
scoped.

## What's missing before scaling past 1 agent

### 1. Agent identity and a real registry
Nothing in the codebase assigns an `agent_id` or lets you enumerate running
agents. `RalphLoopHarness` is instantiated per-workdir with no registry
tying workdirs to agents, owners, or tenants. `MissionAnchor` tracks exactly
one goal system-wide. **Needed:** an `Agent` record (id, owner/tenant,
capability set, current status, workdir/state pointer) in a real table, plus
list/get/terminate endpoints — none of which exist today even as a stub.

### 2. Per-agent circuit breakers and kill switches
`PoisonPill` and `MissionAnchor.circuit_breaker_trigger()` are global — one
trip pauses/locks everyone (`production_risks.md` #11). **Needed:** scope
every lockdown/circuit-breaker check by `agent_id`, with the current global
behavior preserved as an explicit "lock down everything" superset action,
not the only action available.

### 3. A real capability/permission model
`_LEAST_PRIVILEGE_ROLES` in `guardian_scanner.py:21-28` is a 3-entry Python
dict literal (`sub-agent`, `mesh-peer`, `human-operator`) with two fixed
scopes (`read`, `execute`) plus a wildcard. It is not data-driven, not
per-tool, not per-resource, and not extensible without a code change and
redeploy. **Needed:** a capability registry (what tools/resources an agent
type may touch) that's configuration, not a hard-coded dict, so 100 agent
*types* with different permissions don't all funnel through 3 roles.

### 4. Concurrency-safe state, not single-writer JSON files
Every Triumvirate/UAC module persists through either a whole-file
`Path.write_text()` with no locking (`mission_anchor.py`, `hardware_sovereignty.py`,
`meta_cognitive_planner.py`) or an ad hoc per-call `sqlite3.connect()` with
no WAL/busy_timeout set outside `db/sqlite.py` (`memory/store.py`,
`uac/audit_chain.py`, `triumvirate/hardware_sovereignty.py`'s hippocampus).
`RalphLoopHarness` is the only module with any lock at all, and it's a
single advisory file lock with no staleness/expiry (`_acquire_lock()` at
`agent/ralph_loop.py:318-326` — a crashed holder deadlocks every future
run against that workdir forever). **Needed:** one shared, concurrency-safe
state layer (even just consistent WAL-mode SQLite with retry/backoff)
instead of five bespoke JSON-file and ad hoc SQLite patterns.

### 5. Health checks and liveness per agent
`api/health.py`'s `/ready` checks exactly two things: whether Ollama's TCP
port opens and whether `db.healthcheck()` returns "ok" — both are
system-wide, not per-agent. There is no way to ask "is agent X alive,"
"how far along is agent X," or "which agents are stuck" as a queryable
signal beyond manually reading a specific `STATUS.json` off disk if you
already know the workdir. **Needed:** a per-agent heartbeat/health endpoint
that aggregates `RalphLoopHarness` STATUS files (or their replacement) into
one dashboard, not N manual file reads.

### 6. Resource limits beyond one dollar cap
`RalphLoopHarness.Constraints` (budget_cap_usd, max_loops, stall_threshold)
is real and enforced, but it's per-instance only — nothing aggregates spend
or CPU/GPU time across concurrently running agents, and nothing throttles
admission when the shared Ollama backend or shared SQLite files are already
saturated (see `scale_failure_analysis.md`). **Needed:** a system-level
admission controller that knows total concurrent agent count and shared
resource load before spawning agent N+1.

### 7. An actual message/task substrate
There is no queue anywhere in the codebase. Agent execution is either a
synchronous HTTP request (`api/triumvirate.py:/plan`, `/argus/audit`, etc.)
or a direct in-process call into `RalphLoopHarness.execute()`, which then
runs its entire loop (up to 12 iterations, each a blocking Ollama call)
inside that same request/thread. **Needed:** decouple "ask an agent to run"
from "run it now, on this thread" — a real task queue (even a local one)
is a prerequisite for running more agents than there are request-handling
threads.

### 8. Wire UAC into Triumvirate, not just import from it
Today the coupling is one-way and thin: UAC borrows `MissionAnchor` for
scope-locking, nothing else. The audit trail (`AuditChain`), the artifact
store (`AxiomLibrary`), and the narration log (`ObserverLog`) that UAC
built specifically because Triumvirate's `argus_auditor.py` was confirmed
(per that module's own docstring) to be "a pattern-scanning linter... not a
hash chain" are never consulted by Triumvirate's own audit surface
(`/triumvirate/argus/*`). **Needed:** either merge the two audit systems or
expose UAC's `AuditChain`/`AxiomLibrary` through `/triumvirate` so a
100-agent factory has one place to look for tamper-evident provenance, not
two disconnected ones.

### 9. Tenant/agent isolation is real for chat only
`api/tenant_chat.py` + the `X-Tenant-ID` header do genuinely namespace chat
memory sessions (`api/chat.py:46-47`). Nothing else is tenant-scoped:
`MissionAnchor`, `PoisonPill`, `AuditChain`, `AxiomLibrary`, and
`VectorHippocampus` are all single global instances/files regardless of
tenant. A factory of 100+ agents across multiple tenants would have every
agent, from every tenant, sharing one mission scope and one kill switch.

## Sequencing recommendation

Do #4 (concurrency-safe state) and #1 (agent registry) first — every other
item depends on agents being addressable and state being safe to touch from
more than one process. #2 and #3 (per-agent breakers, real capability model)
are the next tier because they're what makes "100 agents" safe rather than
merely possible. #7 (task substrate) and #6 (resource admission control)
are what actually let you run 100 concurrently rather than serially. #8 and
#9 are consolidation work that becomes far cheaper once #1 and #4 exist.
