# What Would Break at 1,000 Agents

This is the centerpiece finding of the review: naming the actual module,
file, and pattern that becomes the bottleneck — not generic
platform-engineering advice. Every item below cites the exact code that
fails, and roughly where on the way from 1 → 1,000 concurrent agents it
starts to hurt.

## 1. SQLite writers with no WAL/busy_timeout outside `db/sqlite.py` — first to break, likely at 10–50 concurrent

`db/sqlite.py:get_connection()` correctly sets `PRAGMA journal_mode=WAL` and
`PRAGMA busy_timeout=5000` (`db/sqlite.py:19-25`). Nothing else does:

- `memory/store.py:MemoryStore._conn()` (`memory/store.py:28-31`) — opens a
  fresh `sqlite3.connect()` per call, no WAL, no busy_timeout. Every
  chat turn does at least one `recent()` read and one `append()` write
  through this path (`api/chat.py:63-67`).
- `uac/audit_chain.py:AuditChain._conn()` (`uac/audit_chain.py:87-90`) — same
  gap. Every UAC stage append (`mission_started`, `manifest_published`, etc.)
  writes to **one shared file**, `data/uac/audit_chain.db`, regardless of
  which agent or mission is running.
- `triumvirate/hardware_sovereignty.py:VectorHippocampus._conn()`
  (`hardware_sovereignty.py:79-82`) — same gap.
- `triumvirate/argus_auditor.py` — raw `sqlite3.connect(_MULCH_DB)` calls
  scattered per-method, same gap.

Without WAL, SQLite's default rollback-journal mode takes an exclusive lock
for the duration of every write transaction; without `busy_timeout`, a
second concurrent writer gets `sqlite3.OperationalError: database is
locked` immediately instead of waiting. At 1,000 agents each doing chat
turns and/or UAC audit-chain appends, this is not a slowdown — it's
unhandled exceptions surfacing as 500s the moment two agents write to the
same file in the same instant, which at that concurrency is constant.

**Fix shape:** apply the same three pragmas everywhere `sqlite3.connect` is
called, or better, route every module through one shared connection-factory
function instead of four independent `_conn()` implementations.

## 2. `RalphLoopHarness`'s file lock has no expiry — first stuck agent, ever

`_acquire_lock()` (`agent/ralph_loop.py:318-326`) is `O_CREAT | O_EXCL` on a
`.ralph_lock` file with the PID written inside, and nothing ever checks
whether that PID is still alive or how old the lock is. If a process
holding the lock is killed (OOM, crash, `kill -9`, container restart —
all normal at scale), the lock file is orphaned forever, and every future
`execute()` against that workdir returns
`ralph_loop:locked` (`agent/ralph_loop.py:412-418`) permanently. This is a
single-agent problem today (one workdir = one lock), but it means the
harness's concurrency model is fundamentally "one agent per workdir, ever" —
there is no path from here to N agents sharing infrastructure without a
redesign, only to N agents each with their own workdir and no shared state
between them (which then hits #1 and #3 instead).

**Fix shape:** add a lock timestamp + liveness check (PID exists, lock age
under a threshold) and steal/clear stale locks; separately, decide whether
the unit of concurrency should stay "one workdir per agent" (fine, but then
say so and build the registry from `sovereign_agent_factory_phase2.md` #1
around it) or move to a real distributed lock.

## 3. `MissionAnchor` / `PoisonPill` — one global file, unlocked writes, global blast radius — breaks correctness before it breaks performance

`MissionAnchor._save()` (`triumvirate/mission_anchor.py:48-50`) is a bare
`Path.write_text()` with **no lock at all** — unlike `RalphLoopHarness`,
this one doesn't even have the file-lock protection. At any concurrency
above 1, two agents calling `/triumvirate/status/lock` or `/status/update`
at close to the same time will race: both read the same starting state,
both write, and one write silently clobbers the other — no error, no
exception, just a lost update. This is worse than a performance ceiling; it
is silent data loss under concurrency, in a module whose whole job is to be
the trustworthy source of truth about mission state.

Compounding this: `MissionAnchor` tracks **exactly one mission for the
entire process** — there is no `agent_id`/`mission_id` keying anywhere in
the schema (`_default_status()`, `triumvirate/mission_anchor.py:20-31`). At
1,000 agents this isn't a contention problem to tune away — the data model
itself only has room for one mission. Every agent would overwrite every
other agent's `goal`, `scope_hash`, and `budget_spent_usd`.

Same story for `PoisonPill`: `detonate()` sets a global `locked_down: true`
that `GuardianScanner.enforce_least_privilege()` checks for *every* caller
regardless of identity (`guardian_scanner.py:185-189`). One agent's bad
script triggering a poison pill — or, as observed on this exact tag, a
stale committed state file — freezes all 1,000 agents' permission checks
at once (see `production_risks.md` #6, #11).

**Fix shape:** key `MissionAnchor`/`PoisonPill` state by `agent_id`/`tenant_id`;
add at minimum a file lock (matching `RalphLoopHarness`'s existing pattern)
around read-modify-write, ideally move off whole-file JSON entirely.

## 4. In-process rate limiting and metrics — breaks the moment you add a second worker process, which you must to use more than one core

`api/app.py`'s rate limiter (`_RUN_RATE_WINDOW: Dict[str, Tuple[float, int]]`,
`app.py:46-49`) and every `prometheus_client` Counter/Gauge in
`observability/metrics.py` live in process memory. Running 1,000 concurrent
agents on one Python process (one GIL) is not viable for CPU-bound work
regardless of I/O concurrency, so the natural next step — multiple uvicorn
workers or multiple processes — silently defeats both systems: each worker
has its own independent rate-limit window (limits become `N × the
configured cap`, trivially), and `/metrics/prometheus` on any one worker
only reflects that worker's traffic, not the fleet's.

**Fix shape:** move rate-limit counters to shared storage (Redis or even
SQLite-with-WAL) and use `prometheus_client`'s multiprocess mode (or push
metrics to a central collector) before scaling past one process.

## 5. `VectorHippocampus` — O(n) Python-loop cosine scan on the request thread

`search()` (`hardware_sovereignty.py:126-144`) loads up to `limit * 10` rows
from SQLite, deserializes every embedding from a JSON blob, and computes
cosine similarity in a pure-Python loop — no ANN index, no batching, no
background execution. Even at moderate corpus size (thousands of chunks)
this is real CPU work done synchronously inside an `async def` request
handler, blocking the event loop for every other concurrent request on that
worker while it runs. At 1,000 agents each calling
`/triumvirate/hippocampus/search`, this single function is the most
concrete example in the repo of "one agent's request materially slows down
every other agent's request on the same process," because it's synchronous
CPU work with no `await` yielding control anywhere inside the loop.
(The real Qdrant-backed path in `api/rag.py` does not have this problem —
which makes it stranger that both exist; see `technical_debt.md` #1.)

**Fix shape:** either delete `VectorHippocampus` in favor of routing
everything through Qdrant, or if it must stay, run the scan in a thread pool
executor and cap corpus size / add a real index.

## 6. Ollama itself is the hard ceiling, and there's no queue in front of it

Every LLM call in the codebase — chat, UAC research prompts, Ralph Loop
research actions — ultimately goes through `local_ai/ollama.py`'s
`httpx.Client` against one `OLLAMA_URL`, i.e. one local model server on one
machine's GPU/CPU. There is no request queue, no backpressure, no batching,
no load balancing across replicas, and no admission control anywhere
upstream of that call. `RalphLoopHarness.execute()` runs its entire loop —
up to `max_loops=12` iterations, each potentially blocking on an Ollama
call — synchronously inside whatever thread invoked it
(`agent/ralph_loop.py:393-423`). 1,000 agents each wanting inference at once
will queue *inside the OS socket layer* against a single Ollama process,
with the application offering no visibility into or control over that
queue — no "how many agents are waiting on inference right now" signal
exists anywhere in `observability/metrics.py`.

**Fix shape:** this is the one item that isn't a code bug so much as a
missing layer: put a real task queue between "agent wants to run" and "LLM
call happens," with a bounded number of in-flight inference calls, before
attempting concurrency anywhere near 1,000. Everything else in this
document assumes agents can eventually get LLM access; this is the
component that decides how many can, at once, at all.

## 7. No agent registry — you cannot even observe the problem

Tying the above together: nothing in the codebase can answer "how many
agents are currently running" or "which agents are waiting on Ollama /
blocked on a stale lock / stuck on a global mission-state race." Every
failure mode above would show up first as unexplained 500s, silently lost
mission updates, or degraded latency with no built-in way to attribute it
to a specific agent, because agents aren't first-class, addressable
entities anywhere in `triumvirate/`, `uac/`, or `agent/ralph_loop.py` (see
`sovereign_agent_factory_phase2.md` #1). At 10 agents this is a curiosity.
At 1,000, it's the difference between debugging a specific failure and
debugging the entire system at once.

## Summary: where the ceiling actually is

Ordered by how few concurrent agents it takes to hit each one:

1. **~2 agents**: `MissionAnchor`/`PoisonPill` unlocked writes lose data or
   lock out every agent (item 3) — this is a correctness bug, not a scale
   one, and it's already present today at concurrency of exactly 2.
2. **~10–50 agents**: SQLite writers without WAL start throwing
   `database is locked` (item 1); one crashed agent permanently blocks its
   workdir with no self-healing (item 2).
3. **~50–200 agents, multi-process**: rate limiting and metrics become
   meaningless the moment you add a second worker process to use more than
   one CPU core (item 4).
4. **~100+ agents with any real corpus**: hippocampus search starts
   stalling the event loop for everyone on that worker (item 5).
5. **Anywhere near 1,000**: the single local Ollama instance, with no
   queue or admission control in front of it, is the actual hard ceiling —
   every other fix in this document only matters if agents can get that far
   before queuing invisibly at the model server (item 6), and there is
   currently no way to even see that queue forming (item 7).
